import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from docflow import (
    ApprovalAuditData,
    AuditDocumentMismatchError,
    AuditEvent,
    AuditEventType,
    AuditPayloadError,
    AuditRevisionError,
    AuditSequenceError,
    AuditTimestampError,
    CorrectionAuditData,
    ExtractionAuditData,
    IngestionAuditData,
    IngestionMetadata,
    ReviewStartedAuditData,
    ReviewStatus,
    ValidationAuditData,
    ValidationDecision,
    ValidationIssue,
    ValidationReasonCode,
    ValidationResult,
    ValidationSeverity,
    VerifiedAuditData,
    append_event,
    apply_correction,
    approve_review,
    audit_trail_to_dict,
    create_audit_trail,
    normalize_document,
    record_correction_applied,
    record_document_approved,
    record_document_ingested,
    record_document_verified,
    record_extraction_completed,
    record_review_started,
    record_validation_completed,
    start_review,
    validate_document,
)

DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
T1 = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
T2 = T1 + timedelta(minutes=1)
T3 = T1 + timedelta(minutes=2)
T4 = T1 + timedelta(minutes=3)
T5 = T1 + timedelta(minutes=4)
T6 = T1 + timedelta(minutes=5)
T7 = T1 + timedelta(minutes=6)


def _clock(value: datetime):
    return lambda: value


def _uuid_factory(*values: int):
    ids = iter(UUID(int=value) for value in values)
    return lambda: next(ids)


def _metadata(document_id: str = DOCUMENT_ID) -> IngestionMetadata:
    return IngestionMetadata(
        document_id=document_id,
        original_filename="form-z2.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        ingested_at=T1,
        provider="nutrient_dws",
        provider_status=200,
        raw_response_path=Path("artifacts/raw/private-response.json"),
        provider_request_id="req-123",
    )


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "document_number": "139",
        "document_date": "2026-06-23",
        "supplier_name": "Sintech",
        "supplier_tax_id": "170840022944",
        "buyer_name": "Buyer",
        "buyer_tax_id": "",
        "currency": "KZT",
        "grand_total": "167881",
        "vat_total": "23156",
        "line_items": [
            {
                "line_number": "1",
                "product_description": "Product",
                "sku": "0001",
                "unit": "шт",
                "quantity": "1",
                "unit_price": "167881",
                "vat_amount": "23156",
                "line_total": "167881",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _session(payload: dict[str, object], created_at: datetime = T4):
    document = normalize_document(payload)
    validation = validate_document(document)
    return start_review(document, validation, clock=_clock(created_at))


def _ingestion_data() -> IngestionAuditData:
    return IngestionAuditData(
        provider="nutrient_dws",
        original_filename="form-z2.pdf",
        mime_type="application/pdf",
        file_size_bytes=2048,
        provider_status=200,
        provider_request_id="req-123",
    )


def _event(
    *,
    document_id: str = DOCUMENT_ID,
    sequence: int = 1,
    revision: int = 0,
    timestamp: datetime = T1,
) -> AuditEvent:
    return AuditEvent(
        event_id=str(UUID(int=sequence)),
        document_id=document_id,
        event_type=AuditEventType.DOCUMENT_INGESTED,
        timestamp=timestamp,
        sequence=sequence,
        revision=revision,
        data=_ingestion_data(),
    )


def test_empty_trail_creation_preserves_document_id() -> None:
    trail = create_audit_trail(DOCUMENT_ID)

    assert trail.document_id == DOCUMENT_ID
    assert trail.events == ()


def test_first_event_sequence_is_one() -> None:
    trail = record_document_ingested(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        uuid_factory=_uuid_factory(1),
    )

    assert trail.events[0].sequence == 1


def test_subsequent_sequences_increment_and_previous_trail_is_unchanged() -> None:
    empty = create_audit_trail(DOCUMENT_ID)
    first = append_event(empty, _event(sequence=1))
    second_event = AuditEvent(
        event_id=str(UUID(int=2)),
        document_id=DOCUMENT_ID,
        event_type=AuditEventType.EXTRACTION_COMPLETED,
        timestamp=T2,
        sequence=2,
        revision=0,
        data=ExtractionAuditData("nutrient_dws", 200, "req-123"),
    )
    second = append_event(first, second_event)

    assert empty.events == ()
    assert [event.sequence for event in first.events] == [1]
    assert [event.sequence for event in second.events] == [1, 2]


def test_event_trail_and_payload_are_immutable() -> None:
    event = _event()
    trail = append_event(create_audit_trail(DOCUMENT_ID), event)

    with pytest.raises(FrozenInstanceError):
        event.sequence = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        trail.document_id = OTHER_DOCUMENT_ID  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        event.data.provider = "changed"  # type: ignore[union-attr,misc]
    assert isinstance(trail.events, tuple)


def test_mutable_payload_container_is_rejected() -> None:
    with pytest.raises(AuditPayloadError, match="immutable audit scalar"):
        CorrectionAuditData(
            field_path="supplier_name",
            old_raw_value={"unsafe": "mutable"},
            new_raw_value="Sintech",
            revision=1,
            corrected_at=T1,
        )


def test_event_ids_are_uuid_strings_and_injected_factory_is_deterministic() -> None:
    trail = record_document_ingested(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        uuid_factory=_uuid_factory(42),
    )

    assert UUID(trail.events[0].event_id) == UUID(int=42)
    assert trail.events[0].event_id == str(UUID(int=42))


def test_default_uuid_factory_generates_a_valid_event_id() -> None:
    trail = record_document_ingested(create_audit_trail(DOCUMENT_ID), _metadata())

    assert str(UUID(trail.events[0].event_id)) == trail.events[0].event_id


def test_timezone_aware_timestamp_is_accepted_and_normalized_to_utc() -> None:
    dubai_time = datetime(2026, 8, 25, 12, 0, tzinfo=timezone(timedelta(hours=4)))

    event = _event(timestamp=dubai_time)

    assert event.timestamp == T1
    assert event.timestamp.tzinfo is UTC


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(AuditTimestampError, match="timezone-aware"):
        _event(timestamp=datetime(2026, 8, 25, 8, 0))


def test_event_for_different_document_is_rejected() -> None:
    trail = create_audit_trail(DOCUMENT_ID)

    with pytest.raises(AuditDocumentMismatchError):
        append_event(trail, _event(document_id=OTHER_DOCUMENT_ID))


def test_skipped_or_duplicate_sequence_is_rejected() -> None:
    trail = append_event(create_audit_trail(DOCUMENT_ID), _event(sequence=1))

    with pytest.raises(AuditSequenceError, match="expected audit sequence 2"):
        append_event(trail, _event(sequence=3))
    with pytest.raises(AuditSequenceError, match="expected audit sequence 2"):
        append_event(trail, _event(sequence=1))


def test_ingestion_adapter_records_only_safe_metadata() -> None:
    trail = record_document_ingested(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        uuid_factory=_uuid_factory(1),
    )
    event = trail.events[0]

    assert event.event_type is AuditEventType.DOCUMENT_INGESTED
    assert event.timestamp == T1
    assert event.data == _ingestion_data()
    assert not hasattr(event.data, "raw_response_path")
    assert not hasattr(event.data, "raw_response")


def test_api_key_and_raw_extraction_never_appear_in_audit_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "NUTRIENT_API_KEY=super-secret"
    monkeypatch.setenv("NUTRIENT_API_KEY", secret)
    trail = record_document_ingested(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        uuid_factory=_uuid_factory(1),
    )
    trail = record_extraction_completed(
        trail,
        _metadata(),
        clock=_clock(T2),
        uuid_factory=_uuid_factory(2),
    )

    serialized = json.dumps(audit_trail_to_dict(trail))
    assert secret not in serialized
    assert "private-response.json" not in serialized
    assert "raw_response" not in serialized


def test_extraction_event_records_only_provider_completion_fields() -> None:
    trail = record_extraction_completed(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        clock=_clock(T2),
        uuid_factory=_uuid_factory(1),
    )
    data = trail.events[0].data

    assert data == ExtractionAuditData("nutrient_dws", 200, "req-123")
    assert not hasattr(data, "raw_json")


def test_ingestion_adapter_rejects_different_document_id() -> None:
    with pytest.raises(AuditDocumentMismatchError):
        record_document_ingested(
            create_audit_trail(DOCUMENT_ID),
            _metadata(OTHER_DOCUMENT_ID),
            uuid_factory=_uuid_factory(1),
        )


def test_validation_event_preserves_decision_count_and_reason_order() -> None:
    issues = (
        ValidationIssue(
            ValidationReasonCode.CURRENCY_MISSING,
            "currency",
            "missing",
            ValidationSeverity.WARNING,
            "present",
            None,
        ),
        ValidationIssue(
            ValidationReasonCode.GRAND_TOTAL_MISMATCH,
            "grand_total",
            "mismatch",
            ValidationSeverity.ERROR,
            Decimal("10"),
            Decimal("11"),
        ),
    )
    result = ValidationResult(ValidationDecision.REVIEW, issues)

    trail = record_validation_completed(
        create_audit_trail(DOCUMENT_ID),
        result,
        revision=0,
        clock=_clock(T3),
        uuid_factory=_uuid_factory(1),
    )
    data = trail.events[0].data

    assert data == ValidationAuditData(
        decision=ValidationDecision.REVIEW,
        issue_count=2,
        reason_codes=(
            ValidationReasonCode.CURRENCY_MISSING,
            ValidationReasonCode.GRAND_TOTAL_MISMATCH,
        ),
    )


def test_review_started_event_records_status_and_revision() -> None:
    session = _session(_valid_payload(currency=None))

    trail = record_review_started(
        create_audit_trail(DOCUMENT_ID),
        session,
        uuid_factory=_uuid_factory(1),
    )
    event = trail.events[0]

    assert event.timestamp == T4
    assert event.revision == 0
    assert event.data == ReviewStartedAuditData(ReviewStatus.REVIEW, 0)


def test_correction_event_preserves_exact_raw_values_revision_and_timestamp() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "167 981,00"
    session = _session(_valid_payload(line_items=[row]))
    corrected = apply_correction(
        session,
        "line_items[0].line_total",
        "167 881,00",
        clock=_clock(T5),
    )

    trail = record_correction_applied(
        create_audit_trail(DOCUMENT_ID),
        corrected.corrections[0],
        uuid_factory=_uuid_factory(1),
    )
    event = trail.events[0]
    data = event.data

    assert event.event_type is AuditEventType.CORRECTION_APPLIED
    assert event.timestamp == T5
    assert event.revision == 1
    assert isinstance(data, CorrectionAuditData)
    assert data.field_path == "line_items[0].line_total"
    assert data.old_raw_value == "167 981,00"
    assert data.new_raw_value == "167 881,00"
    assert data.corrected_at == T5


def test_negative_correction_revision_is_rejected() -> None:
    with pytest.raises(AuditRevisionError):
        CorrectionAuditData(
            field_path="supplier_name",
            old_raw_value="Old",
            new_raw_value="New",
            revision=-1,
            corrected_at=T1,
        )


def test_correction_revisions_cannot_move_backwards_or_repeat() -> None:
    first_data = CorrectionAuditData("field", "a", "b", 2, T1)
    first_event = AuditEvent(
        str(UUID(int=1)),
        DOCUMENT_ID,
        AuditEventType.CORRECTION_APPLIED,
        T1,
        1,
        2,
        first_data,
    )
    trail = append_event(create_audit_trail(DOCUMENT_ID), first_event)
    second_data = CorrectionAuditData("field", "b", "c", 1, T2)
    second_event = AuditEvent(
        str(UUID(int=2)),
        DOCUMENT_ID,
        AuditEventType.CORRECTION_APPLIED,
        T2,
        2,
        1,
        second_data,
    )

    with pytest.raises(AuditRevisionError, match="increase strictly"):
        append_event(trail, second_event)


def test_approval_and_verified_events_record_distinct_action_and_state() -> None:
    approved = approve_review(_session(_valid_payload()), clock=_clock(T7))
    trail = record_document_approved(
        create_audit_trail(DOCUMENT_ID),
        approved,
        uuid_factory=_uuid_factory(1),
    )
    trail = record_document_verified(trail, approved, uuid_factory=_uuid_factory(2))

    approval = trail.events[0]
    verified = trail.events[1]
    assert approval.event_type is AuditEventType.DOCUMENT_APPROVED
    assert approval.data == ApprovalAuditData(ValidationDecision.PASS, ReviewStatus.VERIFIED, 0)
    assert verified.event_type is AuditEventType.DOCUMENT_VERIFIED
    assert verified.data == VerifiedAuditData(ReviewStatus.VERIFIED, 0)


def test_approval_event_rejects_non_verified_session() -> None:
    with pytest.raises(AuditPayloadError, match="VERIFIED"):
        record_document_approved(
            create_audit_trail(DOCUMENT_ID),
            _session(_valid_payload()),
            uuid_factory=_uuid_factory(1),
        )


def test_serialization_is_json_safe_ordered_and_handles_decimal_datetime_enums() -> None:
    data = CorrectionAuditData(
        field_path="line_items[0].line_total",
        old_raw_value=Decimal("167981.00"),
        new_raw_value=Decimal("167881.00"),
        revision=1,
        corrected_at=T5,
    )
    event = AuditEvent(
        str(UUID(int=1)),
        DOCUMENT_ID,
        AuditEventType.CORRECTION_APPLIED,
        T5,
        1,
        1,
        data,
    )

    payload = audit_trail_to_dict(append_event(create_audit_trail(DOCUMENT_ID), event))
    encoded = json.dumps(payload)

    assert payload["events"][0]["event_type"] == "CORRECTION_APPLIED"
    assert payload["events"][0]["timestamp"] == "2026-08-25T08:04:00Z"
    assert payload["events"][0]["data"]["old_raw_value"] == "167981.00"
    assert "CORRECTION_APPLIED" in encoded


def test_integrated_eight_event_lifecycle_is_ordered_and_document_scoped() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "167981"
    initial_session = _session(_valid_payload(line_items=[row]), created_at=T4)
    corrected_session = apply_correction(
        initial_session,
        "line_items[0].line_total",
        "167881",
        clock=_clock(T5),
    )
    approved_session = approve_review(corrected_session, clock=_clock(T7))
    uuid_factory = _uuid_factory(*range(1, 9))

    trail = create_audit_trail(DOCUMENT_ID)
    trail = record_document_ingested(trail, _metadata(), uuid_factory=uuid_factory)
    trail = record_extraction_completed(
        trail, _metadata(), clock=_clock(T2), uuid_factory=uuid_factory
    )
    trail = record_validation_completed(
        trail,
        initial_session.validation_result,
        revision=0,
        clock=_clock(T3),
        uuid_factory=uuid_factory,
    )
    trail = record_review_started(trail, initial_session, uuid_factory=uuid_factory)
    trail = record_correction_applied(
        trail, corrected_session.corrections[0], uuid_factory=uuid_factory
    )
    trail = record_validation_completed(
        trail,
        corrected_session.validation_result,
        revision=1,
        clock=_clock(T6),
        uuid_factory=uuid_factory,
    )
    trail = record_document_approved(trail, approved_session, uuid_factory=uuid_factory)
    trail = record_document_verified(trail, approved_session, uuid_factory=uuid_factory)

    assert len(trail.events) == 8
    assert [event.sequence for event in trail.events] == list(range(1, 9))
    assert {event.document_id for event in trail.events} == {DOCUMENT_ID}
    assert [event.event_type for event in trail.events] == [
        AuditEventType.DOCUMENT_INGESTED,
        AuditEventType.EXTRACTION_COMPLETED,
        AuditEventType.VALIDATION_COMPLETED,
        AuditEventType.REVIEW_STARTED,
        AuditEventType.CORRECTION_APPLIED,
        AuditEventType.VALIDATION_COMPLETED,
        AuditEventType.DOCUMENT_APPROVED,
        AuditEventType.DOCUMENT_VERIFIED,
    ]
    correction = trail.events[4]
    assert isinstance(correction.data, CorrectionAuditData)
    assert correction.data.old_raw_value == "167981"
    assert correction.data.new_raw_value == "167881"
    assert trail.events[-1].event_type is AuditEventType.DOCUMENT_VERIFIED
    assert trail.events[-1].revision == 1


def test_event_sequence_is_deterministic_for_same_inputs() -> None:
    first = record_document_ingested(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        uuid_factory=_uuid_factory(1),
    )
    second = record_document_ingested(
        create_audit_trail(DOCUMENT_ID),
        _metadata(),
        uuid_factory=_uuid_factory(1),
    )

    assert first == second

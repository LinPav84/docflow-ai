import csv
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest

from docflow import (
    CSV_COLUMNS,
    EXPORT_SCHEMA_VERSION,
    AuditExportMismatchError,
    ExportIntegrityError,
    ExportNotAllowedError,
    NormalizedValue,
    ReviewStatus,
    ValidationDecision,
    ValidationResult,
    apply_correction,
    approve_review,
    create_audit_trail,
    export_verified_csv,
    export_verified_json,
    normalize_document,
    record_document_approved,
    record_document_verified,
    start_review,
    validate_document,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "nutrient" / "sintech_run_b.json"
DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"
T1 = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 8, 26, 8, 5, tzinfo=UTC)


def _clock(value: datetime):
    return lambda: value


def _uuid_factory(*values: int):
    ids = iter(UUID(int=value) for value in values)
    return lambda: next(ids)


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "document_number": "000139",
        "document_date": "2026-06-23",
        "supplier_name": "ТОО «Sintech»",
        "supplier_tax_id": "170840022944",
        "buyer_name": "Haileybury Almaty",
        "buyer_tax_id": "",
        "currency": "KZT",
        "responsible_person": "Койлыбаева Ж.Н.",
        "subtotal": "167881.00",
        "vat_total": "23156.00",
        "grand_total": "167881.00",
        "line_items": [
            {
                "line_number": "1",
                "product_description": "ИБП APC SRV1KI",
                "sku": "00000002498",
                "barcode": "0000123456789",
                "unit": "шт",
                "quantity": "1.00",
                "unit_price": "167881.00",
                "vat_amount": "23156.00",
                "line_total": "167881.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _start_session(payload: dict[str, object] | None = None):
    document = normalize_document(payload or _valid_payload())
    return start_review(document, validate_document(document), clock=_clock(T1))


def _verified_session(payload: dict[str, object] | None = None):
    return approve_review(_start_session(payload), clock=_clock(T2))


def _verified_audit(session, document_id: str = DOCUMENT_ID):
    uuid_factory = _uuid_factory(1, 2)
    trail = create_audit_trail(document_id)
    trail = record_document_approved(trail, session, uuid_factory=uuid_factory)
    return record_document_verified(trail, session, uuid_factory=uuid_factory)


def _csv_rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(csv_text, newline="")))


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    return False


def test_verified_sintech_document_exports_canonical_json() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    session = _verified_session(payload)
    trail = _verified_audit(session)

    exported = export_verified_json(session, trail)

    assert exported["schema_version"] == EXPORT_SCHEMA_VERSION == "1.0"
    assert exported["status"] == "VERIFIED"
    assert exported["revision"] == 0
    assert exported["document"]["document_number"] == "139"
    assert exported["document"]["supplier_name"].endswith("«Sintech»")
    assert len(exported["line_items"]) == 1
    json.dumps(exported, ensure_ascii=False)


def test_pass_but_not_verified_is_rejected_for_both_exports() -> None:
    session = _start_session()

    assert session.status is ReviewStatus.PASS
    with pytest.raises(ExportNotAllowedError, match="explicitly VERIFIED"):
        export_verified_json(session, create_audit_trail(DOCUMENT_ID))
    with pytest.raises(ExportNotAllowedError, match="explicitly VERIFIED"):
        export_verified_csv(session)


def test_review_document_is_rejected() -> None:
    session = _start_session(_valid_payload(currency=None))

    assert session.status is ReviewStatus.REVIEW
    with pytest.raises(ExportNotAllowedError):
        export_verified_csv(session)


def test_verified_status_still_requires_pass_validation_decision() -> None:
    session = _verified_session()
    tampered = replace(
        session,
        validation_result=ValidationResult(ValidationDecision.REVIEW, ()),
    )

    with pytest.raises(ExportNotAllowedError, match="PASS"):
        export_verified_csv(tampered)


def test_stale_or_tampered_verified_session_is_rejected() -> None:
    session = _verified_session()
    invalid_document = normalize_document(_valid_payload(currency=None))
    tampered = replace(session, document=invalid_document)

    with pytest.raises(ExportIntegrityError, match="stale or inconsistent"):
        export_verified_csv(tampered)


def test_json_preserves_identifiers_as_strings_with_leading_zeroes() -> None:
    session = _verified_session()

    exported = export_verified_json(session, _verified_audit(session))
    document = exported["document"]
    item = exported["line_items"][0]

    assert document["document_number"] == "000139"
    assert document["supplier_tax_id"] == "170840022944"
    assert isinstance(document["supplier_tax_id"], str)
    assert item["sku"] == "00000002498"
    assert item["barcode"] == "0000123456789"


def test_json_decimal_quantity_and_money_use_plain_precision_preserving_strings() -> None:
    session = _verified_session()

    exported = export_verified_json(session, _verified_audit(session))
    document = exported["document"]
    item = exported["line_items"][0]

    assert item["quantity"] == "1.00"
    assert item["unit_price"] == "167881.00"
    assert item["vat_amount"] == "23156.00"
    assert item["line_total"] == "167881.00"
    assert document["subtotal"] == "167881.00"
    assert document["vat_total"] == "23156.00"
    assert document["grand_total"] == "167881.00"
    assert not _contains_float(exported)


def test_missing_optional_values_become_json_null() -> None:
    session = _verified_session(
        _valid_payload(buyer_tax_id=None, responsible_person=None, subtotal=None)
    )

    exported = export_verified_json(session, _verified_audit(session))

    assert exported["document"]["buyer_tax_id"] is None
    assert exported["document"]["responsible_person"] is None
    assert exported["document"]["subtotal"] is None


def test_multiple_line_items_preserve_source_order_in_json() -> None:
    rows = [
        {
            "line_number": "3",
            "product_description": "Третий",
            "sku": "0003",
            "barcode": "00003",
            "unit": "шт",
            "quantity": "1",
            "unit_price": "10",
            "vat_amount": "1",
            "line_total": "10",
        },
        {
            "line_number": "1",
            "product_description": "Первый",
            "sku": "0001",
            "barcode": "00001",
            "unit": "шт",
            "quantity": "2",
            "unit_price": "10",
            "vat_amount": "2",
            "line_total": "20",
        },
    ]
    session = _verified_session(
        _valid_payload(
            line_items=rows,
            subtotal="30",
            vat_total="3",
            grand_total="30",
        )
    )

    exported = export_verified_json(session, _verified_audit(session))

    assert [item["line_number"] for item in exported["line_items"]] == ["3", "1"]
    assert [item["sku"] for item in exported["line_items"]] == ["0003", "0001"]


def test_json_export_is_deterministic_and_embeds_audit_in_event_order() -> None:
    session = _verified_session()
    trail = _verified_audit(session)

    first = export_verified_json(session, trail)
    second = export_verified_json(session, trail)

    assert first == second
    assert [event["event_type"] for event in first["audit"]["events"]] == [
        "DOCUMENT_APPROVED",
        "DOCUMENT_VERIFIED",
    ]


def test_json_audit_preserves_document_id() -> None:
    session = _verified_session()

    exported = export_verified_json(session, _verified_audit(session))

    assert exported["audit"]["document_id"] == DOCUMENT_ID
    assert {event["document_id"] for event in exported["audit"]["events"]} == {DOCUMENT_ID}


def test_json_rejects_audit_not_ending_in_document_verified() -> None:
    session = _verified_session()
    trail = record_document_approved(
        create_audit_trail(DOCUMENT_ID),
        session,
        uuid_factory=_uuid_factory(1),
    )

    with pytest.raises(AuditExportMismatchError, match="DOCUMENT_VERIFIED"):
        export_verified_json(session, trail)


def test_json_rejects_audit_revision_mismatch() -> None:
    initial = _start_session(_valid_payload(currency=None))
    corrected = apply_correction(initial, "currency", "KZT", clock=_clock(T1))
    revision_one = approve_review(corrected, clock=_clock(T2))
    revision_zero_audit = _verified_audit(_verified_session())

    assert revision_one.revision == 1
    with pytest.raises(AuditExportMismatchError, match="revision"):
        export_verified_json(revision_one, revision_zero_audit)


def test_csv_header_is_exact_and_stable() -> None:
    header = next(csv.reader(StringIO(export_verified_csv(_verified_session()))))

    assert tuple(header) == CSV_COLUMNS
    assert header == [
        "document_number",
        "document_date",
        "supplier_name",
        "supplier_tax_id",
        "buyer_name",
        "buyer_tax_id",
        "currency",
        "responsible_person",
        "line_number",
        "sku",
        "barcode",
        "product_description",
        "unit",
        "quantity",
        "unit_price",
        "vat_amount",
        "line_total",
        "document_subtotal",
        "document_vat_total",
        "document_grand_total",
    ]


def test_csv_has_one_row_per_line_item_with_repeated_document_context() -> None:
    first = _valid_payload()["line_items"][0].copy()
    first.update(line_number="1", line_total="10", unit_price="10", vat_amount="1")
    second = first.copy()
    second.update(line_number="2", sku="0002", line_total="20", unit_price="20")
    session = _verified_session(
        _valid_payload(
            line_items=[first, second],
            subtotal="30",
            vat_total="2",
            grand_total="30",
        )
    )

    rows = _csv_rows(export_verified_csv(session))

    assert len(rows) == 2
    assert [row["line_number"] for row in rows] == ["1", "2"]
    assert [row["sku"] for row in rows] == ["00000002498", "0002"]
    assert {row["document_number"] for row in rows} == {"000139"}
    assert {row["supplier_name"] for row in rows} == {"ТОО «Sintech»"}
    assert {row["document_grand_total"] for row in rows} == {"30"}


def test_csv_preserves_unicode_and_quotes_comma_quote_and_newline() -> None:
    description = 'ИБП, модель "SRV1KI"\nвторая строка'
    row = _valid_payload()["line_items"][0].copy()
    row["product_description"] = description
    session = _verified_session(_valid_payload(line_items=[row]))

    csv_text = export_verified_csv(session)
    parsed = _csv_rows(csv_text)

    assert "ТОО «Sintech»" in csv_text
    assert parsed[0]["product_description"] == description
    assert '""SRV1KI""' in csv_text


def test_csv_none_becomes_empty_cell_and_decimals_remain_plain_strings() -> None:
    session = _verified_session(_valid_payload(buyer_tax_id=None, responsible_person=None))

    row = _csv_rows(export_verified_csv(session))[0]

    assert row["buyer_tax_id"] == ""
    assert row["responsible_person"] == ""
    assert row["quantity"] == "1.00"
    assert row["unit_price"] == "167881.00"
    assert row["document_grand_total"] == "167881.00"


def test_csv_avoids_scientific_notation() -> None:
    document = normalize_document(
        _valid_payload(
            line_items=[
                {
                    **_valid_payload()["line_items"][0],
                    "quantity": "1000",
                    "unit_price": "1",
                    "vat_amount": "0",
                    "line_total": "1000",
                }
            ],
            subtotal="1000",
            vat_total="0",
            grand_total="1000",
        )
    )
    item = replace(
        document.line_items[0],
        quantity=NormalizedValue(raw_value="1000", value=Decimal("1E+3")),
    )
    document = replace(document, line_items=(item,))
    session = start_review(document, validate_document(document), clock=_clock(T1))
    verified = approve_review(session, clock=_clock(T2))

    row = _csv_rows(export_verified_csv(verified))[0]

    assert row["quantity"] == "1000"
    assert "E+" not in row["quantity"]


def test_csv_preserves_source_line_order_and_is_deterministic() -> None:
    rows = [
        {
            **_valid_payload()["line_items"][0],
            "line_number": "2",
            "sku": "0002",
            "unit_price": "10",
            "line_total": "10",
            "vat_amount": "1",
        },
        {
            **_valid_payload()["line_items"][0],
            "line_number": "1",
            "sku": "0001",
            "unit_price": "20",
            "line_total": "20",
            "vat_amount": "2",
        },
    ]
    session = _verified_session(
        _valid_payload(line_items=rows, subtotal="30", vat_total="3", grand_total="30")
    )

    first = export_verified_csv(session)
    second = export_verified_csv(session)

    assert first == second
    assert [row["line_number"] for row in _csv_rows(first)] == ["2", "1"]


def test_exports_do_not_mutate_session_or_audit_trail() -> None:
    session = _verified_session()
    trail = _verified_audit(session)
    original_session = deepcopy(session)
    original_trail = deepcopy(trail)

    export_verified_json(session, trail)
    export_verified_csv(session)

    assert session == original_session
    assert trail == original_trail


def test_exports_do_not_expose_raw_provider_or_internal_artifact_data() -> None:
    session = _verified_session()
    exported = export_verified_json(session, _verified_audit(session))
    encoded = json.dumps(exported, ensure_ascii=False)

    assert "raw_response" not in encoded
    assert "NUTRIENT_API_KEY" not in encoded
    assert "authorization" not in encoded.lower()
    assert "artifacts/raw" not in encoded

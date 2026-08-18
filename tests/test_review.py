import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from docflow import (
    ApprovalNotAllowedError,
    CorrectionValueError,
    InvalidCorrectionPathError,
    ReviewStatus,
    StaleValidationError,
    ValidationDecision,
    ValidationIssue,
    ValidationReasonCode,
    ValidationResult,
    ValidationSeverity,
    apply_correction,
    approve_review,
    normalize_document,
    start_review,
    validate_document,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "nutrient" / "sintech_run_b.json"
CREATED_AT = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)
CORRECTED_AT = CREATED_AT + timedelta(minutes=5)
APPROVED_AT = CREATED_AT + timedelta(minutes=10)


def _clock(value: datetime):
    return lambda: value


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "document_number": "139",
        "document_date": "2026-06-23",
        "supplier_name": "Sintech",
        "supplier_tax_id": "170840022944",
        "buyer_name": "Buyer",
        "buyer_tax_id": "",
        "currency": "KZT",
        "responsible_person": "Person",
        "subtotal": "167881",
        "vat_total": "23156",
        "grand_total": "167881",
        "line_items": [
            {
                "line_number": "1",
                "product_description": "Product",
                "sku": "00000002498",
                "barcode": "",
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


def _session(payload: dict[str, object] | None = None):
    document = normalize_document(payload or _valid_payload())
    return start_review(document, validate_document(document), clock=_clock(CREATED_AT))


def test_real_sintech_pass_starts_in_pass_state() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    document = normalize_document(payload)

    session = start_review(document, validate_document(document), clock=_clock(CREATED_AT))

    assert session.status is ReviewStatus.PASS
    assert session.validation_result.decision is ValidationDecision.PASS
    assert session.revision == 0
    assert session.corrections == ()
    assert session.created_at == session.updated_at == CREATED_AT


def test_explicit_approval_changes_pass_to_verified_without_revision() -> None:
    session = _session()

    approved = approve_review(session, clock=_clock(APPROVED_AT))

    assert approved.status is ReviewStatus.VERIFIED
    assert approved.validation_result.decision is ValidationDecision.PASS
    assert approved.revision == 0
    assert approved.updated_at == APPROVED_AT
    assert session.status is ReviewStatus.PASS


def test_review_document_cannot_be_approved() -> None:
    session = _session(_valid_payload(currency=None))

    with pytest.raises(ApprovalNotAllowedError, match="must pass validation"):
        approve_review(session)


def test_matching_review_result_creates_review_session() -> None:
    document = normalize_document(_valid_payload(currency=None))
    validation_result = validate_document(document)

    session = start_review(document, validation_result, clock=_clock(CREATED_AT))

    assert validation_result.decision is ValidationDecision.REVIEW
    assert session.status is ReviewStatus.REVIEW
    assert session.validation_result == validation_result


def test_review_document_with_fabricated_pass_is_rejected() -> None:
    document = normalize_document(_valid_payload(currency=None))
    fabricated_pass = ValidationResult(decision=ValidationDecision.PASS, issues=())

    with pytest.raises(StaleValidationError) as error:
        start_review(document, fabricated_pass, clock=_clock(CREATED_AT))

    assert error.value.supplied_decision is ValidationDecision.PASS
    assert error.value.fresh_decision is ValidationDecision.REVIEW


def test_pass_document_with_fabricated_review_is_rejected() -> None:
    document = normalize_document(_valid_payload())
    fabricated_review = ValidationResult(decision=ValidationDecision.REVIEW, issues=())

    with pytest.raises(StaleValidationError) as error:
        start_review(document, fabricated_review, clock=_clock(CREATED_AT))

    assert error.value.supplied_decision is ValidationDecision.REVIEW
    assert error.value.fresh_decision is ValidationDecision.PASS


def test_same_decision_with_different_issue_set_is_rejected() -> None:
    document = normalize_document(_valid_payload(currency=None))
    different_issue = ValidationIssue(
        reason_code=ValidationReasonCode.REQUIRED_FIELD_MISSING,
        field_path="document_number",
        message="different issue",
        severity=ValidationSeverity.WARNING,
        expected="present",
        actual=None,
    )
    fabricated_review = ValidationResult(
        decision=ValidationDecision.REVIEW,
        issues=(different_issue,),
    )

    with pytest.raises(StaleValidationError) as error:
        start_review(document, fabricated_review, clock=_clock(CREATED_AT))

    assert error.value.supplied_decision is error.value.fresh_decision


def test_failed_start_review_does_not_mutate_inputs_or_create_state() -> None:
    document = normalize_document(_valid_payload(currency=None))
    original_document = deepcopy(document)
    fabricated_pass = ValidationResult(decision=ValidationDecision.PASS, issues=())
    original_result = deepcopy(fabricated_pass)

    def unexpected_clock() -> datetime:
        raise AssertionError("clock must not run for rejected initialization")

    with pytest.raises(StaleValidationError):
        start_review(document, fabricated_pass, clock=unexpected_clock)

    assert document == original_document
    assert fabricated_pass == original_result


def test_approval_retains_stale_validation_defense_in_depth() -> None:
    session = _session()
    changed_document = normalize_document(_valid_payload(currency=None))
    stale_session = replace(session, document=changed_document)

    with pytest.raises(ApprovalNotAllowedError, match="stale"):
        approve_review(stale_session)


def test_arithmetic_error_starts_in_review() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "167981"

    session = _session(_valid_payload(line_items=[row]))

    assert session.status is ReviewStatus.REVIEW
    assert session.validation_result.decision is ValidationDecision.REVIEW


def test_correcting_line_total_revalidates_to_pass_but_not_verified() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "167 981,00"
    session = _session(_valid_payload(line_items=[row]))

    corrected = apply_correction(
        session,
        "line_items[0].line_total",
        "167 881,00",
        clock=_clock(CORRECTED_AT),
    )

    assert corrected.status is ReviewStatus.PASS
    assert corrected.status is not ReviewStatus.VERIFIED
    assert corrected.validation_result.decision is ValidationDecision.PASS
    assert corrected.validation_result == validate_document(corrected.document)
    assert corrected.document.line_items[0].line_total.value == Decimal("167881.00")
    assert corrected.revision == 1
    assert corrected.corrections[0].old_raw_value == "167 981,00"
    assert corrected.corrections[0].new_raw_value == "167 881,00"


def test_explicit_approval_after_correction_produces_verified() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "167981"
    session = _session(_valid_payload(line_items=[row]))
    corrected = apply_correction(session, "line_items[0].line_total", "167881")

    approved = approve_review(corrected, clock=_clock(APPROVED_AT))

    assert corrected.status is ReviewStatus.PASS
    assert approved.status is ReviewStatus.VERIFIED
    assert approved.revision == corrected.revision == 1


def test_quantity_correction_reuses_normalization_and_revalidates() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["quantity"] = "2"
    session = _session(_valid_payload(line_items=[row]))

    corrected = apply_correction(session, "line_items[0].quantity", "1")

    assert corrected.document.line_items[0].quantity.raw_value == "1"
    assert corrected.document.line_items[0].quantity.value == Decimal("1")
    assert corrected.validation_result.decision is ValidationDecision.PASS


def test_invalid_numeric_correction_is_rejected_without_state_change() -> None:
    session = _session()

    with pytest.raises(CorrectionValueError) as error:
        apply_correction(session, "line_items[0].quantity", "abc")

    assert error.value.field_path == "line_items[0].quantity"
    assert error.value.raw_value == "abc"
    assert session.revision == 0
    assert session.corrections == ()
    assert session.document.line_items[0].quantity.raw_value == "1"


def test_empty_correction_follows_existing_normalization_behavior() -> None:
    session = _session()

    corrected = apply_correction(session, "supplier_name", "")

    assert corrected.document.supplier_name.raw_value == ""
    assert corrected.document.supplier_name.value is None
    assert corrected.status is ReviewStatus.REVIEW
    assert corrected.revision == 1


def test_correcting_supplier_tax_id_can_clear_review() -> None:
    session = _session(_valid_payload(supplier_tax_id="bad"))

    corrected = apply_correction(session, "supplier_tax_id", "170840022944")

    assert session.status is ReviewStatus.REVIEW
    assert corrected.status is ReviewStatus.PASS
    assert corrected.document.supplier_tax_id.value == "170840022944"


def test_correcting_missing_currency_can_clear_review() -> None:
    session = _session(_valid_payload(currency=None))

    corrected = apply_correction(session, "currency", "KZT")

    assert session.status is ReviewStatus.REVIEW
    assert corrected.status is ReviewStatus.PASS
    assert corrected.document.currency.value == "KZT"


@pytest.mark.parametrize(
    "field_path",
    [
        "line_items[-1].quantity",
        "line_items.quantity",
        "foo.bar",
        "line_items[01].quantity",
        "line_items[0]",
    ],
)
def test_invalid_field_paths_are_rejected(field_path: str) -> None:
    session = _session()

    with pytest.raises(InvalidCorrectionPathError) as error:
        apply_correction(session, field_path, "1")

    assert error.value.field_path == field_path
    assert session.revision == 0


def test_out_of_range_line_index_is_rejected() -> None:
    session = _session()

    with pytest.raises(InvalidCorrectionPathError, match="out of range"):
        apply_correction(session, "line_items[999].quantity", "1")


def test_unknown_line_field_is_rejected() -> None:
    session = _session()

    with pytest.raises(InvalidCorrectionPathError, match="unsupported line-item field"):
        apply_correction(session, "line_items[0].unknown_field", "value")


def test_correction_history_is_append_only_with_exact_revisions_and_values() -> None:
    initial = _session()
    first = apply_correction(
        initial, "responsible_person", "First Person", clock=_clock(CORRECTED_AT)
    )
    second = apply_correction(
        first,
        "responsible_person",
        "Second Person",
        clock=_clock(APPROVED_AT),
    )

    assert initial.corrections == ()
    assert first.revision == 1
    assert second.revision == 2
    assert len(first.corrections) == 1
    assert len(second.corrections) == 2
    assert second.corrections[0] is first.corrections[0]
    assert second.corrections[0].old_raw_value == "Person"
    assert second.corrections[0].new_raw_value == "First Person"
    assert second.corrections[0].revision == 1
    assert second.corrections[1].old_raw_value == "First Person"
    assert second.corrections[1].new_raw_value == "Second Person"
    assert second.corrections[1].revision == 2


def test_previous_session_and_line_item_identity_remain_unchanged() -> None:
    second_row = _valid_payload()["line_items"][0].copy()
    second_row.update(line_number="2", sku="0002", quantity="2", line_total="335762")
    first_row = _valid_payload()["line_items"][0].copy()
    payload = _valid_payload(
        line_items=[first_row, second_row],
        grand_total="503643",
        vat_total="46312",
    )
    session = _session(payload)

    corrected = apply_correction(session, "line_items[1].product_description", "Second")

    assert len(session.document.line_items) == len(corrected.document.line_items) == 2
    assert [item.line_number.raw_value for item in corrected.document.line_items] == ["1", "2"]
    assert [item.sku.raw_value for item in corrected.document.line_items] == [
        "00000002498",
        "0002",
    ]
    assert session.document.line_items[1].product_description.raw_value == "Product"
    assert corrected.document.line_items[1].product_description.raw_value == "Second"


def test_models_are_immutable() -> None:
    session = _session()
    corrected = apply_correction(session, "responsible_person", "New Person")

    with pytest.raises(FrozenInstanceError):
        corrected.revision = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        corrected.corrections[0].revision = 99  # type: ignore[misc]


def test_each_correction_validation_matches_the_effective_document() -> None:
    session = _session(_valid_payload(currency=None, supplier_tax_id="bad"))

    first = apply_correction(session, "currency", "KZT")
    second = apply_correction(first, "supplier_tax_id", "170840022944")

    assert first.validation_result == validate_document(first.document)
    assert second.validation_result == validate_document(second.document)
    assert first.status is ReviewStatus.REVIEW
    assert second.status is ReviewStatus.PASS


def test_correction_after_verification_requires_fresh_explicit_approval() -> None:
    verified = approve_review(_session())

    corrected = apply_correction(verified, "responsible_person", "New Person")

    assert corrected.status is ReviewStatus.PASS
    assert corrected.status is not ReviewStatus.VERIFIED


def test_correction_is_deterministic_with_injected_clock() -> None:
    session = _session(_valid_payload(currency=None))

    first = apply_correction(session, "currency", "KZT", clock=_clock(CORRECTED_AT))
    second = apply_correction(session, "currency", "KZT", clock=_clock(CORRECTED_AT))

    assert first == second

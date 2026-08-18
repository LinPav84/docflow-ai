import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from docflow import (
    ValidationDecision,
    ValidationReasonCode,
    ValidationSeverity,
    normalize_document,
    validate_document,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "nutrient" / "sintech_run_b.json"


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "document_number": "139",
        "document_date": "2026-06-23",
        "supplier_name": "Sintech",
        "supplier_tax_id": "170840022944",
        "buyer_tax_id": "",
        "currency": "KZT",
        "grand_total": "100.00",
        "vat_total": "12.00",
        "line_items": [
            {
                "line_number": "1",
                "product_description": "Product",
                "sku": "0001",
                "unit": "pcs",
                "quantity": "2",
                "unit_price": "50.00",
                "vat_amount": "12.00",
                "line_total": "100.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _validate(**overrides: object):
    return validate_document(normalize_document(_valid_payload(**overrides)))


def _codes(result) -> list[ValidationReasonCode]:
    return [issue.reason_code for issue in result.issues]


def test_real_sintech_normalized_document_passes() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    result = validate_document(normalize_document(payload))

    assert result.decision is ValidationDecision.PASS
    assert result.issues == ()


@pytest.mark.parametrize(
    ("overrides", "field_path"),
    [
        ({"document_number": None}, "document_number"),
        ({"document_date": None}, "document_date"),
        ({"supplier_name": None}, "supplier_name"),
        ({"grand_total": None}, "grand_total"),
    ],
)
def test_missing_required_document_field_returns_review(
    overrides: dict[str, object], field_path: str
) -> None:
    result = _validate(**overrides)

    assert result.decision is ValidationDecision.REVIEW
    assert any(
        issue.reason_code is ValidationReasonCode.REQUIRED_FIELD_MISSING
        and issue.field_path == field_path
        for issue in result.issues
    )


def test_missing_currency_has_one_specific_issue() -> None:
    result = _validate(currency=None)

    currency_issues = [issue for issue in result.issues if issue.field_path == "currency"]
    assert result.decision is ValidationDecision.REVIEW
    assert [issue.reason_code for issue in currency_issues] == [
        ValidationReasonCode.CURRENCY_MISSING
    ]


def test_no_line_items_returns_review() -> None:
    result = _validate(line_items=[])

    assert result.decision is ValidationDecision.REVIEW
    assert any(
        issue.reason_code is ValidationReasonCode.REQUIRED_FIELD_MISSING
        and issue.field_path == "line_items"
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "field_name",
    ["product_description", "quantity", "unit_price", "line_total"],
)
def test_missing_required_line_item_field_returns_review(field_name: str) -> None:
    row = _valid_payload()["line_items"][0].copy()
    row[field_name] = None

    result = _validate(line_items=[row])

    assert result.decision is ValidationDecision.REVIEW
    assert any(
        issue.reason_code is ValidationReasonCode.REQUIRED_FIELD_MISSING
        and issue.field_path == f"line_items[0].{field_name}"
        for issue in result.issues
    )


def test_correct_line_and_grand_total_arithmetic_has_no_mismatch() -> None:
    result = _validate()

    assert ValidationReasonCode.LINE_TOTAL_MISMATCH not in _codes(result)
    assert ValidationReasonCode.GRAND_TOTAL_MISMATCH not in _codes(result)


def test_incorrect_line_arithmetic_and_grand_total_both_return_review() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row.update(quantity="1", unit_price="167881", line_total="167981")

    result = _validate(line_items=[row], grand_total="167881")

    assert result.decision is ValidationDecision.REVIEW
    assert ValidationReasonCode.LINE_TOTAL_MISMATCH in _codes(result)
    assert ValidationReasonCode.GRAND_TOTAL_MISMATCH in _codes(result)


def test_grand_total_mismatch_is_reported_independently() -> None:
    result = _validate(grand_total="100.03")

    issue = next(
        issue
        for issue in result.issues
        if issue.reason_code is ValidationReasonCode.GRAND_TOTAL_MISMATCH
    )
    assert issue.field_path == "grand_total"
    assert issue.expected == Decimal("100.00")
    assert issue.actual == Decimal("100.03")


def test_missing_line_total_does_not_invent_grand_total_input() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = None

    result = _validate(line_items=[row])

    assert ValidationReasonCode.GRAND_TOTAL_MISMATCH not in _codes(result)


def test_vat_total_match_has_no_issue() -> None:
    assert ValidationReasonCode.VAT_TOTAL_MISMATCH not in _codes(_validate())


def test_vat_total_mismatch_returns_review() -> None:
    result = _validate(vat_total="12.03")

    assert result.decision is ValidationDecision.REVIEW
    assert ValidationReasonCode.VAT_TOTAL_MISMATCH in _codes(result)


@pytest.mark.parametrize(
    "overrides",
    [
        {"vat_total": None},
        {
            "line_items": [
                {
                    **_valid_payload()["line_items"][0],
                    "vat_amount": None,
                }
            ]
        },
    ],
)
def test_absent_vat_values_do_not_create_invented_vat_issue(overrides: dict[str, object]) -> None:
    result = _validate(**overrides)

    assert ValidationReasonCode.VAT_TOTAL_MISMATCH not in _codes(result)


def test_valid_supplier_tax_id_and_empty_optional_buyer_tax_id_have_no_warning() -> None:
    result = _validate(supplier_tax_id="170840022944", buyer_tax_id="")

    assert ValidationReasonCode.TAX_ID_FORMAT_WARNING not in _codes(result)


@pytest.mark.parametrize("tax_id", ["123", "１２３４５６７８９０１２", "12345678901A"])
def test_invalid_supplier_tax_id_returns_warning_review(tax_id: str) -> None:
    result = _validate(supplier_tax_id=tax_id)

    issue = next(
        issue
        for issue in result.issues
        if issue.reason_code is ValidationReasonCode.TAX_ID_FORMAT_WARNING
    )
    assert result.decision is ValidationDecision.REVIEW
    assert issue.field_path == "supplier_tax_id"
    assert issue.severity is ValidationSeverity.WARNING
    assert issue.actual == tax_id


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_non_positive_quantity_returns_cross_field_review(quantity: str) -> None:
    row = _valid_payload()["line_items"][0].copy()
    row.update(quantity=quantity, line_total="0", vat_amount="0")

    result = _validate(line_items=[row], grand_total="0", vat_total="0")

    assert result.decision is ValidationDecision.REVIEW
    assert any(
        issue.reason_code is ValidationReasonCode.CROSS_FIELD_INCONSISTENCY
        and issue.field_path == "line_items[0].quantity"
        for issue in result.issues
    )


@pytest.mark.parametrize("difference", ["0.01", "0.02"])
def test_absolute_tolerance_boundary_passes(difference: str) -> None:
    actual = Decimal("100.00") + Decimal(difference)
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = str(actual)

    result = _validate(line_items=[row], grand_total=str(actual))

    assert ValidationReasonCode.LINE_TOTAL_MISMATCH not in _codes(result)


@pytest.mark.parametrize("difference", ["0.05", "0.10"])
def test_relative_tolerance_boundary_passes(difference: str) -> None:
    expected = Decimal("1000.00")
    actual = expected + Decimal(difference)
    row = _valid_payload()["line_items"][0].copy()
    row.update(quantity="10", unit_price="100", line_total=str(actual))

    result = _validate(line_items=[row], grand_total=str(actual))

    assert ValidationReasonCode.LINE_TOTAL_MISMATCH not in _codes(result)


def test_difference_beyond_both_tolerances_returns_review() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "100.03"

    result = _validate(line_items=[row], grand_total="100.03")

    assert ValidationReasonCode.LINE_TOTAL_MISMATCH in _codes(result)


@pytest.mark.parametrize(
    ("actual", "expect_mismatch"),
    [("0.02", False), ("0.03", True)],
)
def test_zero_expected_amount_uses_absolute_tolerance_safely(
    actual: str, expect_mismatch: bool
) -> None:
    row = _valid_payload()["line_items"][0].copy()
    row.update(quantity="0", unit_price="100", line_total=actual, vat_amount="0")

    result = _validate(line_items=[row], grand_total=actual, vat_total="0")

    assert (ValidationReasonCode.LINE_TOTAL_MISMATCH in _codes(result)) is expect_mismatch


def test_arithmetic_issue_values_are_decimal_never_float() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row["line_total"] = "100.03"

    result = _validate(line_items=[row], grand_total="100.03")
    issue = next(
        issue
        for issue in result.issues
        if issue.reason_code is ValidationReasonCode.LINE_TOTAL_MISMATCH
    )

    assert isinstance(issue.expected, Decimal)
    assert isinstance(issue.actual, Decimal)
    assert not isinstance(issue.expected, float)
    assert not isinstance(issue.actual, float)


def test_validation_is_deterministic_ordered_immutable_and_does_not_mutate_input() -> None:
    row = _valid_payload()["line_items"][0].copy()
    row.update(product_description=None, quantity="0", line_total="100.03")
    document = normalize_document(
        _valid_payload(
            document_number=None,
            supplier_tax_id="bad",
            line_items=[row],
            grand_total="99",
            vat_total="13",
        )
    )
    original = deepcopy(document)

    first = validate_document(document)
    second = validate_document(document)

    assert first == second
    assert document == original
    assert [(issue.reason_code, issue.field_path) for issue in first.issues] == [
        (ValidationReasonCode.REQUIRED_FIELD_MISSING, "document_number"),
        (ValidationReasonCode.TAX_ID_FORMAT_WARNING, "supplier_tax_id"),
        (ValidationReasonCode.REQUIRED_FIELD_MISSING, "line_items[0].product_description"),
        (ValidationReasonCode.CROSS_FIELD_INCONSISTENCY, "line_items[0].quantity"),
        (ValidationReasonCode.LINE_TOTAL_MISMATCH, "line_items[0].line_total"),
        (ValidationReasonCode.GRAND_TOTAL_MISMATCH, "grand_total"),
        (ValidationReasonCode.VAT_TOTAL_MISMATCH, "vat_total"),
    ]
    with pytest.raises(FrozenInstanceError):
        first.decision = ValidationDecision.FAIL  # type: ignore[misc]


def test_structurally_invalid_api_input_is_rejected_without_fabricated_result() -> None:
    with pytest.raises(TypeError, match="NormalizedDocument"):
        validate_document({})  # type: ignore[arg-type]

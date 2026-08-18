import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from docflow import NormalizationError, normalize_document

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "nutrient" / "sintech_run_b.json"


def _document(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "document_number": "1",
        "document_date": "2026-06-23",
        "supplier_tax_id": "170840022944",
        "line_items": [],
    }
    payload.update(overrides)
    return payload


def test_real_sintech_fixture_normalizes_expected_accounting_values() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    result = normalize_document(payload)

    assert result.document_number.value == "139"
    assert result.document_date.value == "2026-06-23"
    assert result.supplier_tax_id.value == "170840022944"
    assert result.buyer_tax_id.value is None
    assert result.grand_total.value == Decimal("167881.00")
    assert result.subtotal.value == Decimal("167881.00")
    assert result.vat_total.value == Decimal("23156.00")
    assert len(result.line_items) == 1

    item = result.line_items[0]
    assert item.quantity.value == Decimal("1")
    assert item.sku.value == "00000002498"
    assert item.unit_price.value == Decimal("167881.00")
    assert item.line_total.value == Decimal("167881.00")
    assert item.vat_amount.value == Decimal("23156.00")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 250,50", Decimal("1250.50")),
        ("167 881.00", Decimal("167881.00")),
        ("23 156,00", Decimal("23156.00")),
        ("1,5", Decimal("1.5")),
        ("10.25", Decimal("10.25")),
        ("1", Decimal("1")),
    ],
)
def test_supported_decimal_formats(raw: str, expected: Decimal) -> None:
    result = normalize_document(_document(grand_total=raw))

    assert result.grand_total.value == expected
    assert isinstance(result.grand_total.value, Decimal)


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_empty_and_whitespace_only_strings_become_none(raw: str) -> None:
    result = normalize_document(_document(buyer_tax_id=raw, supplier_name=raw, grand_total=raw))

    assert result.buyer_tax_id.value is None
    assert result.supplier_name.value is None
    assert result.grand_total.value is None


def test_none_input_values_remain_none() -> None:
    result = normalize_document(_document(buyer_tax_id=None, grand_total=None, document_date=None))

    assert result.buyer_tax_id.value is None
    assert result.grand_total.value is None
    assert result.document_date.value is None


def test_identifiers_remain_strings_and_keep_leading_zeroes() -> None:
    result = normalize_document(
        _document(
            document_number="000139",
            supplier_tax_id="00170840022944",
            buyer_tax_id="000000000001",
            line_items=[{"sku": "00000002498", "barcode": "0000123456789"}],
        )
    )

    assert result.document_number.value == "000139"
    assert result.supplier_tax_id.value == "00170840022944"
    assert result.buyer_tax_id.value == "000000000001"
    assert result.line_items[0].sku.value == "00000002498"
    assert result.line_items[0].barcode.value == "0000123456789"
    assert all(
        isinstance(value, str)
        for value in (
            result.document_number.value,
            result.supplier_tax_id.value,
            result.buyer_tax_id.value,
            result.line_items[0].sku.value,
            result.line_items[0].barcode.value,
        )
    )


def test_iso_date_is_preserved() -> None:
    result = normalize_document(_document(document_date="2026-06-23"))

    assert result.document_date.value == "2026-06-23"


@pytest.mark.parametrize("raw", ["23/06/2026", "06-07-2026", "2026-02-30"])
def test_non_iso_ambiguous_or_invalid_dates_are_rejected(raw: str) -> None:
    with pytest.raises(NormalizationError, match="document_date"):
        normalize_document(_document(document_date=raw))


def test_each_line_item_is_normalized_independently_without_row_changes() -> None:
    rows = [
        {
            "line_number": "01",
            "quantity": "1,5",
            "unit_price": "1 250,50",
            "line_total": "1 875,75",
            "vat_amount": "225,09",
            "sku": "0001",
        },
        {
            "line_number": "02",
            "quantity": "2",
            "unit_price": "10.25",
            "line_total": "20.50",
            "vat_amount": None,
            "sku": None,
        },
    ]

    result = normalize_document(_document(line_items=rows))

    assert len(result.line_items) == 2
    assert result.line_items[0].line_number.value == "01"
    assert result.line_items[0].quantity.value == Decimal("1.5")
    assert result.line_items[0].line_total.value == Decimal("1875.75")
    assert result.line_items[1].line_number.value == "02"
    assert result.line_items[1].quantity.value == Decimal("2")
    assert result.line_items[1].line_total.value == Decimal("20.50")
    assert result.line_items[1].vat_amount.value is None
    assert result.line_items[1].sku.value is None


@pytest.mark.parametrize("raw", ["12,34,56", "1 23,45", "abc", "--10", "1.2.3"])
def test_malformed_numbers_raise_controlled_error(raw: str) -> None:
    with pytest.raises(NormalizationError) as error:
        normalize_document(_document(grand_total=raw))

    assert error.value.field_path == "grand_total"
    assert error.value.raw_value == raw


@pytest.mark.parametrize("raw", ["1,234", "1.234", "1,234.56", "1.234,56"])
def test_ambiguous_numbers_raise_controlled_error(raw: str) -> None:
    with pytest.raises(NormalizationError, match="ambiguous"):
        normalize_document(_document(grand_total=raw))


@pytest.mark.parametrize("raw", [10.25, True])
def test_unsafe_numeric_types_are_rejected(raw: object) -> None:
    with pytest.raises(NormalizationError, match="unsafe"):
        normalize_document(_document(grand_total=raw))


def test_raw_values_are_preserved_exactly() -> None:
    result = normalize_document(
        _document(
            document_number=" 000139 ",
            supplier_name="  Sintech  ",
            grand_total=" 167 881,00 ",
            line_items=[{"quantity": " 1,5 ", "sku": " 00000002498 "}],
        )
    )

    assert result.document_number.raw_value == " 000139 "
    assert result.document_number.value == "000139"
    assert result.supplier_name.raw_value == "  Sintech  "
    assert result.supplier_name.value == "Sintech"
    assert result.grand_total.raw_value == " 167 881,00 "
    assert result.grand_total.value == Decimal("167881.00")
    assert result.line_items[0].quantity.raw_value == " 1,5 "
    assert result.line_items[0].sku.raw_value == " 00000002498 "


def test_missing_values_are_not_inferred() -> None:
    result = normalize_document(_document(line_items=[{}]))

    assert result.buyer_tax_id.value is None
    assert result.vat_total.value is None
    assert result.line_items[0].sku.value is None
    assert result.line_items[0].vat_amount.value is None


def test_invalid_line_item_includes_its_path() -> None:
    with pytest.raises(NormalizationError) as error:
        normalize_document(_document(line_items=[{"quantity": "1"}, {"quantity": "bad"}]))

    assert error.value.field_path == "line_items[1].quantity"


def test_normalization_is_deterministic_and_does_not_mutate_input() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    original = deepcopy(payload)

    first = normalize_document(payload)
    second = normalize_document(payload)

    assert first == second
    assert payload == original

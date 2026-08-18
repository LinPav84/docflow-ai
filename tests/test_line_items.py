import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from docflow import LineItemMappingError, map_line_items_from_extraction

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "nutrient" / "sintech_extract_response.json"


def _response(line_items: object = None, *, include_line_items: bool = True) -> dict[str, object]:
    data: dict[str, object] = {}
    if include_line_items:
        data["line_items"] = line_items
    return {"output": {"data": data}}


def test_real_sintech_extract_response_maps_one_raw_row_exactly() -> None:
    response = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw_row = response["output"]["data"]["line_items"][0]

    result = map_line_items_from_extraction(response)

    assert len(result) == 1
    item = result[0]
    assert item.source_index == 0
    assert item.source_path == "line_items[0]"
    assert item.line_number == raw_row["line_number"] == "1"
    assert item.product_description == raw_row["product_description"]
    assert item.sku == raw_row["sku"] == "00000002498"
    assert item.unit == raw_row["unit"] == "шт"
    assert item.quantity == raw_row["quantity"] == "1"
    assert item.unit_price == raw_row["unit_price"] == "167 881,00"
    assert item.vat_amount == raw_row["vat_amount"] == "23 156,00"
    assert item.line_total == raw_row["line_total"] == "167 881,00"


def test_source_order_indexes_and_paths_are_preserved_without_sorting() -> None:
    rows = [
        {"line_number": "3", "sku": "third"},
        {"line_number": "1", "sku": "first"},
        {"line_number": "2", "sku": "second"},
    ]

    result = map_line_items_from_extraction(_response(rows))

    assert [item.line_number for item in result] == ["3", "1", "2"]
    assert [item.sku for item in result] == ["third", "first", "second"]
    assert [item.source_index for item in result] == [0, 1, 2]
    assert [item.source_path for item in result] == [
        "line_items[0]",
        "line_items[1]",
        "line_items[2]",
    ]


def test_duplicate_rows_are_not_removed_or_merged() -> None:
    row = {"line_number": "1", "sku": "same", "quantity": "1"}

    result = map_line_items_from_extraction(_response([row.copy(), row.copy()]))

    assert len(result) == 2
    assert result[0].sku == result[1].sku == "same"
    assert result[0].source_index == 0
    assert result[1].source_index == 1


@pytest.mark.parametrize(
    "response",
    [_response(include_line_items=False), _response(None)],
    ids=["missing", "null"],
)
def test_missing_or_null_line_items_maps_to_empty_tuple(response: dict[str, object]) -> None:
    assert map_line_items_from_extraction(response) == ()


@pytest.mark.parametrize("raw_value", ["bad", {}, 7, True])
def test_non_list_line_items_raises_controlled_error(raw_value: object) -> None:
    with pytest.raises(LineItemMappingError) as error:
        map_line_items_from_extraction(_response(raw_value))

    assert error.value.field_path == "line_items"
    assert error.value.raw_value == raw_value
    assert error.value.reason == "expected a list or null"


def test_non_object_row_raises_path_aware_controlled_error() -> None:
    with pytest.raises(LineItemMappingError) as error:
        map_line_items_from_extraction(_response([{}, "bad-row"]))

    assert error.value.field_path == "line_items[1]"
    assert error.value.raw_value == "bad-row"
    assert error.value.reason == "expected an object"


def test_missing_fields_remain_none_and_empty_strings_remain_empty() -> None:
    result = map_line_items_from_extraction(_response([{"line_number": "", "quantity": None}]))

    item = result[0]
    assert item.line_number == ""
    assert item.quantity is None
    assert item.product_description is None
    assert item.sku is None
    assert item.unit is None
    assert item.unit_price is None
    assert item.vat_amount is None
    assert item.line_total is None


def test_identifiers_text_quantity_and_money_remain_untouched_strings() -> None:
    raw_description = "  OCR Product / MEC/1000VA  "
    result = map_line_items_from_extraction(
        _response(
            [
                {
                    "sku": "00000002498",
                    "product_description": raw_description,
                    "quantity": "1",
                    "unit_price": "167 881,00",
                    "vat_amount": "23 156,00",
                    "line_total": "167 881,00",
                }
            ]
        )
    )

    item = result[0]
    assert item.sku == "00000002498"
    assert item.product_description == raw_description
    assert item.quantity == "1"
    assert item.unit_price == "167 881,00"
    assert item.vat_amount == "23 156,00"
    assert item.line_total == "167 881,00"
    assert all(
        isinstance(value, str)
        for value in (item.quantity, item.unit_price, item.vat_amount, item.line_total)
    )


def test_unknown_provider_fields_are_ignored_without_crashing() -> None:
    result = map_line_items_from_extraction(
        _response([{"line_number": "1", "provider_confidence": 0.91}])
    )

    assert len(result) == 1
    assert result[0].line_number == "1"
    assert not hasattr(result[0], "provider_confidence")


def test_field_path_helper_is_deterministic_and_model_is_immutable() -> None:
    item = map_line_items_from_extraction(_response([{"quantity": "1"}]))[0]

    assert item.field_path("quantity") == "line_items[0].quantity"
    assert item.field_path("unit_price") == "line_items[0].unit_price"
    assert item.field_path("line_total") == "line_items[0].line_total"
    with pytest.raises(FrozenInstanceError):
        item.source_index = 9  # type: ignore[misc]


def test_mapping_is_deterministic_and_does_not_mutate_input() -> None:
    response = _response(
        [
            {"line_number": "2", "quantity": "", "extra": {"nested": True}},
            {"line_number": "1", "quantity": None},
        ]
    )
    original = deepcopy(response)

    first = map_line_items_from_extraction(response)
    second = map_line_items_from_extraction(response)

    assert first == second
    assert response == original


@pytest.mark.parametrize(
    ("response", "expected_path"),
    [
        (None, "$"),
        ({}, "output"),
        ({"output": None}, "output"),
        ({"output": {}}, "output.data"),
        ({"output": {"data": "bad"}}, "output.data"),
    ],
)
def test_malformed_extraction_envelope_raises_path_aware_error(
    response: object, expected_path: str
) -> None:
    with pytest.raises(LineItemMappingError) as error:
        map_line_items_from_extraction(response)

    assert error.value.field_path == expected_path

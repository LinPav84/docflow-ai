"""Deterministic structural mapping for extracted line items."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class LineItemMappingError(ValueError):
    """A controlled failure for malformed extraction or line-item structure."""

    def __init__(self, field_path: str, raw_value: object, reason: str) -> None:
        self.field_path = field_path
        self.raw_value = raw_value
        self.reason = reason
        super().__init__(f"{field_path}: {reason}; raw value={_safe_raw_value(raw_value)}")


@dataclass(frozen=True, slots=True)
class MappedLineItem:
    """One extracted source row with untouched values and stable provenance."""

    source_index: int
    source_path: str
    line_number: object | None
    product_description: object | None
    sku: object | None
    unit: object | None
    quantity: object | None
    unit_price: object | None
    vat_amount: object | None
    line_total: object | None

    def field_path(self, field_name: str) -> str:
        """Return the deterministic source path for one field in this row."""
        return f"{self.source_path}.{field_name}"


def map_line_items_from_extraction(response_json: object) -> tuple[MappedLineItem, ...]:
    """Map ``output.data.line_items`` without normalization, sorting, or inference."""
    response = _require_mapping(response_json, "$", "expected an extraction response object")
    output = _require_mapping(response.get("output"), "output", "expected an object")
    data = _require_mapping(output.get("data"), "output.data", "expected an object")

    if "line_items" not in data or data["line_items"] is None:
        return ()

    raw_line_items = data["line_items"]
    if not isinstance(raw_line_items, list):
        raise LineItemMappingError("line_items", raw_line_items, "expected a list or null")

    return tuple(_map_line_item(raw_item, index) for index, raw_item in enumerate(raw_line_items))


def _map_line_item(raw_item: object, index: int) -> MappedLineItem:
    source_path = f"line_items[{index}]"
    if not isinstance(raw_item, Mapping):
        raise LineItemMappingError(source_path, raw_item, "expected an object")

    return MappedLineItem(
        source_index=index,
        source_path=source_path,
        line_number=raw_item.get("line_number"),
        product_description=raw_item.get("product_description"),
        sku=raw_item.get("sku"),
        unit=raw_item.get("unit"),
        quantity=raw_item.get("quantity"),
        unit_price=raw_item.get("unit_price"),
        vat_amount=raw_item.get("vat_amount"),
        line_total=raw_item.get("line_total"),
    )


def _require_mapping(raw_value: object, field_path: str, reason: str) -> Mapping[str, object]:
    if not isinstance(raw_value, Mapping):
        raise LineItemMappingError(field_path, raw_value, reason)
    return raw_value


def _safe_raw_value(raw_value: object) -> str:
    if isinstance(raw_value, Mapping):
        return "<object>"
    if isinstance(raw_value, list):
        return "<list>"
    return repr(raw_value)

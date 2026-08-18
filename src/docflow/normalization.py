"""Deterministic normalization of Nutrient DWS accounting extractions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import NoReturn

from docflow.models import NormalizedDocument, NormalizedLineItem, NormalizedValue

_PLAIN_NUMBER = re.compile(r"^[+-]?\d+$")
_DECIMAL_NUMBER = re.compile(r"^[+-]?\d+[.,]\d+$")
_SPACED_NUMBER = re.compile(r"^[+-]?\d{1,3}(?: \d{3})+(?:[.,]\d+)?$")


class NormalizationError(ValueError):
    """A controlled failure for a value that cannot be normalized safely."""

    def __init__(self, field_path: str, raw_value: object, reason: str) -> None:
        self.field_path = field_path
        self.raw_value = raw_value
        self.reason = reason
        super().__init__(f"{field_path}: {reason}; raw value={raw_value!r}")


def normalize_document(payload: Mapping[str, object]) -> NormalizedDocument:
    """Normalize one extracted document without inference or business validation."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")

    raw_line_items = payload.get("line_items")
    if raw_line_items is None:
        line_items: tuple[NormalizedLineItem, ...] = ()
    elif isinstance(raw_line_items, list):
        line_items = tuple(
            _normalize_line_item(item, index) for index, item in enumerate(raw_line_items)
        )
    else:
        raise NormalizationError("line_items", raw_line_items, "expected a list or null")

    return NormalizedDocument(
        buyer_name=_text(payload.get("buyer_name"), "buyer_name"),
        buyer_tax_id=_identifier(payload.get("buyer_tax_id"), "buyer_tax_id"),
        currency=_text(payload.get("currency"), "currency"),
        document_date=_date(payload.get("document_date"), "document_date"),
        document_number=_identifier(payload.get("document_number"), "document_number"),
        grand_total=_decimal(payload.get("grand_total"), "grand_total"),
        line_items=line_items,
        responsible_person=_text(payload.get("responsible_person"), "responsible_person"),
        subtotal=_decimal(payload.get("subtotal"), "subtotal"),
        supplier_name=_text(payload.get("supplier_name"), "supplier_name"),
        supplier_tax_id=_identifier(payload.get("supplier_tax_id"), "supplier_tax_id"),
        vat_total=_decimal(payload.get("vat_total"), "vat_total"),
    )


def _normalize_line_item(raw_item: object, index: int) -> NormalizedLineItem:
    path = f"line_items[{index}]"
    if not isinstance(raw_item, Mapping):
        raise NormalizationError(path, raw_item, "expected an object")

    return NormalizedLineItem(
        line_number=_identifier(raw_item.get("line_number"), f"{path}.line_number"),
        line_total=_decimal(raw_item.get("line_total"), f"{path}.line_total"),
        product_description=_text(
            raw_item.get("product_description"), f"{path}.product_description"
        ),
        quantity=_decimal(raw_item.get("quantity"), f"{path}.quantity"),
        sku=_identifier(raw_item.get("sku"), f"{path}.sku"),
        barcode=_identifier(raw_item.get("barcode"), f"{path}.barcode"),
        unit=_text(raw_item.get("unit"), f"{path}.unit"),
        unit_price=_decimal(raw_item.get("unit_price"), f"{path}.unit_price"),
        vat_amount=_decimal(raw_item.get("vat_amount"), f"{path}.vat_amount"),
    )


def _empty_to_none(raw_value: object, field_path: str) -> object | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        return stripped or None
    raise NormalizationError(field_path, raw_value, "expected a string or null")


def _text(raw_value: object, field_path: str) -> NormalizedValue[str]:
    return NormalizedValue(raw_value=raw_value, value=_empty_to_none(raw_value, field_path))


def _identifier(raw_value: object, field_path: str) -> NormalizedValue[str]:
    """Normalize identifiers without numeric conversion or loss of leading zeroes."""
    return _text(raw_value, field_path)


def _date(raw_value: object, field_path: str) -> NormalizedValue[str]:
    value = _empty_to_none(raw_value, field_path)
    if value is None:
        return NormalizedValue(raw_value=raw_value, value=None)

    assert isinstance(value, str)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(field_path, raw_value, "expected an ISO YYYY-MM-DD date")
    try:
        normalized = date.fromisoformat(value).isoformat()
    except ValueError:
        _fail(field_path, raw_value, "invalid ISO date")
    return NormalizedValue(raw_value=raw_value, value=normalized)


def _decimal(raw_value: object, field_path: str) -> NormalizedValue[Decimal]:
    if raw_value is None:
        return NormalizedValue(raw_value=None, value=None)
    if isinstance(raw_value, (bool, float)):
        _fail(field_path, raw_value, "float and boolean inputs are unsafe for accounting values")
    if isinstance(raw_value, Decimal):
        if not raw_value.is_finite():
            _fail(field_path, raw_value, "non-finite decimal is not supported")
        return NormalizedValue(raw_value=raw_value, value=raw_value)
    if isinstance(raw_value, int):
        return NormalizedValue(raw_value=raw_value, value=Decimal(raw_value))
    if not isinstance(raw_value, str):
        _fail(field_path, raw_value, "expected a string, Decimal, integer, or null")

    value = raw_value.strip()
    if not value:
        return NormalizedValue(raw_value=raw_value, value=None)

    value = value.replace("\u00a0", " ").replace("\u202f", " ")
    if "," in value and "." in value:
        _fail(field_path, raw_value, "mixed decimal separators are ambiguous")

    if " " in value:
        if not _SPACED_NUMBER.fullmatch(value):
            _fail(field_path, raw_value, "invalid thousands grouping")
    elif not (_PLAIN_NUMBER.fullmatch(value) or _DECIMAL_NUMBER.fullmatch(value)):
        _fail(field_path, raw_value, "malformed numeric value")

    separator = "," if "," in value else "." if "." in value else None
    if separator is not None:
        fractional_digits = len(value.rsplit(separator, 1)[1])
        if " " not in value and fractional_digits == 3:
            _fail(field_path, raw_value, "separator with three trailing digits is ambiguous")

    canonical = value.replace(" ", "").replace(",", ".")
    try:
        normalized = Decimal(canonical)
    except InvalidOperation:
        _fail(field_path, raw_value, "malformed numeric value")
    if not normalized.is_finite():
        _fail(field_path, raw_value, "non-finite decimal is not supported")
    return NormalizedValue(raw_value=raw_value, value=normalized)


def _fail(field_path: str, raw_value: object, reason: str) -> NoReturn:
    raise NormalizationError(field_path, raw_value, reason)

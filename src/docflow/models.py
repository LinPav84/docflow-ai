"""Typed results produced by deterministic normalization."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NormalizedValue(Generic[T]):
    """A normalized value paired with the exact extracted source value."""

    raw_value: object
    value: T | None


TextValue = NormalizedValue[str]
DecimalValue = NormalizedValue[Decimal]


@dataclass(frozen=True, slots=True)
class NormalizedLineItem:
    """One independently normalized document row."""

    line_number: TextValue
    line_total: DecimalValue
    product_description: TextValue
    quantity: DecimalValue
    sku: TextValue
    barcode: TextValue
    unit: TextValue
    unit_price: DecimalValue
    vat_amount: DecimalValue


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """Known accounting fields from one Nutrient extraction."""

    buyer_name: TextValue
    buyer_tax_id: TextValue
    currency: TextValue
    document_date: TextValue
    document_number: TextValue
    grand_total: DecimalValue
    line_items: tuple[NormalizedLineItem, ...]
    responsible_person: TextValue
    subtotal: DecimalValue
    supplier_name: TextValue
    supplier_tax_id: TextValue
    vat_total: DecimalValue

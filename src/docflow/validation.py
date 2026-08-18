"""Deterministic accounting validation for normalized DocFlow documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from docflow.models import NormalizedDocument, NormalizedLineItem, NormalizedValue

ABSOLUTE_TOLERANCE = Decimal("0.02")
RELATIVE_TOLERANCE = Decimal("0.0001")


class ValidationDecision(StrEnum):
    """Document-level outcome produced by deterministic validation."""

    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


class ValidationSeverity(StrEnum):
    """Severity of one validation issue."""

    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationReasonCode(StrEnum):
    """Stable v1 reason codes for downstream review and audit consumers."""

    REQUIRED_FIELD_MISSING = "REQUIRED_FIELD_MISSING"
    CURRENCY_MISSING = "CURRENCY_MISSING"
    LINE_TOTAL_MISMATCH = "LINE_TOTAL_MISMATCH"
    GRAND_TOTAL_MISMATCH = "GRAND_TOTAL_MISMATCH"
    VAT_TOTAL_MISMATCH = "VAT_TOTAL_MISMATCH"
    TAX_ID_FORMAT_WARNING = "TAX_ID_FORMAT_WARNING"
    CROSS_FIELD_INCONSISTENCY = "CROSS_FIELD_INCONSISTENCY"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One deterministic, path-aware accounting validation issue."""

    reason_code: ValidationReasonCode
    field_path: str
    message: str
    severity: ValidationSeverity
    expected: object | None
    actual: object | None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Immutable validation decision and ordered issues."""

    decision: ValidationDecision
    issues: tuple[ValidationIssue, ...]


def validate_document(document: NormalizedDocument) -> ValidationResult:
    """Validate one normalized document without mutation, inference, or correction."""
    if not isinstance(document, NormalizedDocument):
        raise TypeError("document must be a NormalizedDocument")

    issues: list[ValidationIssue] = []
    _validate_required_document_fields(document, issues)
    _validate_tax_ids(document, issues)
    _validate_line_items(document.line_items, issues)
    _validate_grand_total(document, issues)
    _validate_vat_total(document, issues)

    decision = ValidationDecision.PASS if not issues else ValidationDecision.REVIEW
    return ValidationResult(decision=decision, issues=tuple(issues))


def _validate_required_document_fields(
    document: NormalizedDocument, issues: list[ValidationIssue]
) -> None:
    required_fields = (
        ("document_number", document.document_number.value),
        ("document_date", document.document_date.value),
        ("supplier_name", document.supplier_name.value),
    )
    for field_path, value in required_fields:
        if value is None:
            issues.append(_required_issue(field_path))

    if document.currency.value is None:
        issues.append(
            ValidationIssue(
                reason_code=ValidationReasonCode.CURRENCY_MISSING,
                field_path="currency",
                message="Currency is required for accounting review.",
                severity=ValidationSeverity.WARNING,
                expected="present",
                actual=None,
            )
        )

    if document.grand_total.value is None:
        issues.append(_required_issue("grand_total"))
    if not document.line_items:
        issues.append(
            ValidationIssue(
                reason_code=ValidationReasonCode.REQUIRED_FIELD_MISSING,
                field_path="line_items",
                message="At least one line item is required.",
                severity=ValidationSeverity.WARNING,
                expected=">= 1",
                actual=0,
            )
        )


def _validate_tax_ids(document: NormalizedDocument, issues: list[ValidationIssue]) -> None:
    for field_path, value in (
        ("supplier_tax_id", document.supplier_tax_id.value),
        ("buyer_tax_id", document.buyer_tax_id.value),
    ):
        if value is not None and re.fullmatch(r"[0-9]{12}", value) is None:
            issues.append(
                ValidationIssue(
                    reason_code=ValidationReasonCode.TAX_ID_FORMAT_WARNING,
                    field_path=field_path,
                    message="Kazakhstan tax ID must contain exactly 12 ASCII digits.",
                    severity=ValidationSeverity.WARNING,
                    expected="12 ASCII digits",
                    actual=value,
                )
            )


def _validate_line_items(
    line_items: tuple[NormalizedLineItem, ...], issues: list[ValidationIssue]
) -> None:
    for index, item in enumerate(line_items):
        path = f"line_items[{index}]"
        for field_name, value in (
            ("product_description", item.product_description.value),
            ("quantity", item.quantity.value),
            ("unit_price", item.unit_price.value),
            ("line_total", item.line_total.value),
        ):
            if value is None:
                issues.append(_required_issue(f"{path}.{field_name}"))

        quantity = _decimal_value(item.quantity, f"{path}.quantity")
        unit_price = _decimal_value(item.unit_price, f"{path}.unit_price")
        line_total = _decimal_value(item.line_total, f"{path}.line_total")

        if quantity is not None and quantity <= 0:
            issues.append(
                ValidationIssue(
                    reason_code=ValidationReasonCode.CROSS_FIELD_INCONSISTENCY,
                    field_path=f"{path}.quantity",
                    message="Line-item quantity must be greater than zero.",
                    severity=ValidationSeverity.ERROR,
                    expected="> 0",
                    actual=quantity,
                )
            )

        if quantity is not None and unit_price is not None and line_total is not None:
            expected_line_total = quantity * unit_price
            if not _within_tolerance(expected_line_total, line_total):
                issues.append(
                    ValidationIssue(
                        reason_code=ValidationReasonCode.LINE_TOTAL_MISMATCH,
                        field_path=f"{path}.line_total",
                        message="Line total does not match quantity multiplied by unit price.",
                        severity=ValidationSeverity.ERROR,
                        expected=expected_line_total,
                        actual=line_total,
                    )
                )


def _validate_grand_total(document: NormalizedDocument, issues: list[ValidationIssue]) -> None:
    grand_total = _decimal_value(document.grand_total, "grand_total")
    if grand_total is None or not document.line_items:
        return

    line_totals = tuple(
        _decimal_value(item.line_total, f"line_items[{index}].line_total")
        for index, item in enumerate(document.line_items)
    )
    if any(value is None for value in line_totals):
        return

    expected_grand_total = sum(
        (value for value in line_totals if value is not None), start=Decimal("0")
    )
    if not _within_tolerance(expected_grand_total, grand_total):
        issues.append(
            ValidationIssue(
                reason_code=ValidationReasonCode.GRAND_TOTAL_MISMATCH,
                field_path="grand_total",
                message="Grand total does not match the sum of line totals.",
                severity=ValidationSeverity.ERROR,
                expected=expected_grand_total,
                actual=grand_total,
            )
        )


def _validate_vat_total(document: NormalizedDocument, issues: list[ValidationIssue]) -> None:
    vat_total = _decimal_value(document.vat_total, "vat_total")
    if vat_total is None or not document.line_items:
        return

    vat_amounts = tuple(
        _decimal_value(item.vat_amount, f"line_items[{index}].vat_amount")
        for index, item in enumerate(document.line_items)
    )
    if any(value is None for value in vat_amounts):
        return

    expected_vat_total = sum(
        (value for value in vat_amounts if value is not None), start=Decimal("0")
    )
    if not _within_tolerance(expected_vat_total, vat_total):
        issues.append(
            ValidationIssue(
                reason_code=ValidationReasonCode.VAT_TOTAL_MISMATCH,
                field_path="vat_total",
                message="VAT total does not match the sum of line-item VAT amounts.",
                severity=ValidationSeverity.ERROR,
                expected=expected_vat_total,
                actual=vat_total,
            )
        )


def _required_issue(field_path: str) -> ValidationIssue:
    return ValidationIssue(
        reason_code=ValidationReasonCode.REQUIRED_FIELD_MISSING,
        field_path=field_path,
        message="Required accounting field is missing.",
        severity=ValidationSeverity.WARNING,
        expected="present",
        actual=None,
    )


def _decimal_value(value: NormalizedValue[Decimal], field_path: str) -> Decimal | None:
    normalized = value.value
    if normalized is not None and not isinstance(normalized, Decimal):
        raise TypeError(f"{field_path}.value must be a Decimal or None")
    return normalized


def _within_tolerance(expected: Decimal, actual: Decimal) -> bool:
    difference = abs(actual - expected)
    if difference <= ABSOLUTE_TOLERANCE:
        return True
    if expected == 0:
        return False
    return difference / abs(expected) <= RELATIVE_TOLERANCE

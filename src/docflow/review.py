"""Immutable human-review state and correction workflow for DocFlow documents."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from docflow.models import NormalizedDocument, NormalizedLineItem
from docflow.normalization import NormalizationError, normalize_document
from docflow.validation import ValidationDecision, ValidationResult, validate_document

Clock = Callable[[], datetime]

_DOCUMENT_FIELDS = frozenset(
    {
        "document_number",
        "document_date",
        "supplier_name",
        "supplier_tax_id",
        "buyer_name",
        "buyer_tax_id",
        "currency",
        "grand_total",
        "vat_total",
        "subtotal",
        "responsible_person",
    }
)
_LINE_ITEM_FIELDS = frozenset(
    {
        "line_number",
        "product_description",
        "sku",
        "barcode",
        "unit",
        "quantity",
        "unit_price",
        "vat_amount",
        "line_total",
    }
)
_LINE_ITEM_PATH = re.compile(r"line_items\[(0|[1-9][0-9]*)\]\.([a-z_]+)")


class ReviewStatus(StrEnum):
    """Explicit document state in the human-review workflow."""

    PROCESSING = "PROCESSING"
    PASS = "PASS"
    REVIEW = "REVIEW"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class ReviewError(ValueError):
    """Base class for controlled review-domain failures."""


class StaleValidationError(ReviewError):
    """The supplied validation result does not match the current document."""

    def __init__(
        self,
        supplied_decision: ValidationDecision,
        fresh_decision: ValidationDecision,
    ) -> None:
        self.supplied_decision = supplied_decision
        self.fresh_decision = fresh_decision
        super().__init__(
            "supplied validation result does not match fresh document validation; "
            f"supplied={supplied_decision.value}, fresh={fresh_decision.value}"
        )


class InvalidCorrectionPathError(ReviewError):
    """The requested correction path is unsupported or unavailable."""

    def __init__(self, field_path: str, reason: str) -> None:
        self.field_path = field_path
        self.reason = reason
        super().__init__(f"{field_path}: {reason}")


class CorrectionValueError(ReviewError):
    """A raw correction could not be normalized safely."""

    def __init__(self, field_path: str, raw_value: object, reason: str) -> None:
        self.field_path = field_path
        self.raw_value = raw_value
        self.reason = reason
        super().__init__(f"{field_path}: {reason}; raw value={_safe_raw_value(raw_value)}")


class ApprovalNotAllowedError(ReviewError):
    """The current document cannot be explicitly verified."""


@dataclass(frozen=True, slots=True)
class ReviewCorrection:
    """One append-only raw-value correction record."""

    field_path: str
    old_raw_value: object
    new_raw_value: object
    revision: int
    corrected_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewSession:
    """Immutable document, validation, and correction state for human review."""

    document: NormalizedDocument
    validation_result: ValidationResult
    status: ReviewStatus
    corrections: tuple[ReviewCorrection, ...]
    revision: int
    created_at: datetime
    updated_at: datetime


def start_review(
    document: NormalizedDocument,
    validation_result: ValidationResult,
    *,
    clock: Clock | None = None,
) -> ReviewSession:
    """Create revision zero from an existing normalized document and validation result."""
    if not isinstance(document, NormalizedDocument):
        raise TypeError("document must be a NormalizedDocument")
    if not isinstance(validation_result, ValidationResult):
        raise TypeError("validation_result must be a ValidationResult")

    fresh_validation = validate_document(document)
    if fresh_validation != validation_result:
        raise StaleValidationError(validation_result.decision, fresh_validation.decision)

    now = (clock or _utc_now)()
    return ReviewSession(
        document=document,
        validation_result=fresh_validation,
        status=_status_from_decision(fresh_validation.decision),
        corrections=(),
        revision=0,
        created_at=now,
        updated_at=now,
    )


def apply_correction(
    session: ReviewSession,
    field_path: str,
    raw_value: object,
    *,
    clock: Clock | None = None,
) -> ReviewSession:
    """Apply one raw correction through normalization and immediate revalidation."""
    target = _parse_correction_path(field_path, len(session.document.line_items))
    payload = _raw_document_payload(session.document)

    if target.line_index is None:
        old_raw_value = payload[target.field_name]
        payload[target.field_name] = raw_value
    else:
        line_items = payload["line_items"]
        assert isinstance(line_items, list)
        row = line_items[target.line_index]
        assert isinstance(row, dict)
        old_raw_value = row[target.field_name]
        row[target.field_name] = raw_value

    try:
        corrected_document = normalize_document(payload)
    except NormalizationError as error:
        raise CorrectionValueError(field_path, raw_value, error.reason) from error

    validation_result = validate_document(corrected_document)
    revision = session.revision + 1
    now = (clock or _utc_now)()
    correction = ReviewCorrection(
        field_path=field_path,
        old_raw_value=old_raw_value,
        new_raw_value=raw_value,
        revision=revision,
        corrected_at=now,
    )
    return replace(
        session,
        document=corrected_document,
        validation_result=validation_result,
        status=_status_from_decision(validation_result.decision),
        corrections=(*session.corrections, correction),
        revision=revision,
        updated_at=now,
    )


def approve_review(session: ReviewSession, *, clock: Clock | None = None) -> ReviewSession:
    """Explicitly verify a document only after a fresh successful validation."""
    fresh_validation = validate_document(session.document)
    if fresh_validation != session.validation_result:
        raise ApprovalNotAllowedError("review validation is stale; revalidation is required")
    if fresh_validation.decision is not ValidationDecision.PASS:
        raise ApprovalNotAllowedError("document must pass validation before approval")

    return replace(
        session,
        validation_result=fresh_validation,
        status=ReviewStatus.VERIFIED,
        updated_at=(clock or _utc_now)(),
    )


@dataclass(frozen=True, slots=True)
class _CorrectionTarget:
    field_name: str
    line_index: int | None


def _parse_correction_path(field_path: str, line_count: int) -> _CorrectionTarget:
    if field_path in _DOCUMENT_FIELDS:
        return _CorrectionTarget(field_name=field_path, line_index=None)

    match = _LINE_ITEM_PATH.fullmatch(field_path)
    if match is None:
        raise InvalidCorrectionPathError(field_path, "unsupported correction path")

    line_index = int(match.group(1))
    field_name = match.group(2)
    if field_name not in _LINE_ITEM_FIELDS:
        raise InvalidCorrectionPathError(field_path, "unsupported line-item field")
    if line_index >= line_count:
        raise InvalidCorrectionPathError(field_path, "line-item index is out of range")
    return _CorrectionTarget(field_name=field_name, line_index=line_index)


def _raw_document_payload(document: NormalizedDocument) -> dict[str, object]:
    return {
        "document_number": document.document_number.raw_value,
        "document_date": document.document_date.raw_value,
        "supplier_name": document.supplier_name.raw_value,
        "supplier_tax_id": document.supplier_tax_id.raw_value,
        "buyer_name": document.buyer_name.raw_value,
        "buyer_tax_id": document.buyer_tax_id.raw_value,
        "currency": document.currency.raw_value,
        "grand_total": document.grand_total.raw_value,
        "vat_total": document.vat_total.raw_value,
        "subtotal": document.subtotal.raw_value,
        "responsible_person": document.responsible_person.raw_value,
        "line_items": [_raw_line_item_payload(item) for item in document.line_items],
    }


def _raw_line_item_payload(item: NormalizedLineItem) -> dict[str, object]:
    return {
        "line_number": item.line_number.raw_value,
        "product_description": item.product_description.raw_value,
        "sku": item.sku.raw_value,
        "barcode": item.barcode.raw_value,
        "unit": item.unit.raw_value,
        "quantity": item.quantity.raw_value,
        "unit_price": item.unit_price.raw_value,
        "vat_amount": item.vat_amount.raw_value,
        "line_total": item.line_total.raw_value,
    }


def _status_from_decision(decision: ValidationDecision) -> ReviewStatus:
    return {
        ValidationDecision.PASS: ReviewStatus.PASS,
        ValidationDecision.REVIEW: ReviewStatus.REVIEW,
        ValidationDecision.FAIL: ReviewStatus.FAILED,
    }[decision]


def _safe_raw_value(raw_value: object) -> str:
    if isinstance(raw_value, (dict, list, tuple, set)):
        return f"<{type(raw_value).__name__}>"
    return repr(raw_value)


def _utc_now() -> datetime:
    return datetime.now(UTC)

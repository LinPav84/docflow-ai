"""Pure VERIFIED-only JSON and CSV exports for DocFlow accounting documents."""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

from docflow.audit import AuditEventType, AuditTrail, audit_trail_to_dict
from docflow.models import NormalizedDocument, NormalizedLineItem
from docflow.review import ReviewSession, ReviewStatus
from docflow.validation import ValidationDecision, validate_document

EXPORT_SCHEMA_VERSION = "1.0"

CSV_COLUMNS = (
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
)


class ExportError(ValueError):
    """Base class for controlled export-domain failures."""


class ExportNotAllowedError(ExportError):
    """The current review session is not eligible for export."""


class ExportIntegrityError(ExportError):
    """The current verified session fails a defensive integrity check."""


class AuditExportMismatchError(ExportIntegrityError):
    """The audit trail does not represent the current verified revision."""


def export_verified_json(session: ReviewSession, audit_trail: AuditTrail) -> dict[str, object]:
    """Return the canonical JSON-ready export for one verified document."""
    _require_verified_session(session)
    _require_matching_audit(session, audit_trail)

    document = session.document
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "status": session.status.value,
        "revision": session.revision,
        "document": _document_values(document),
        "line_items": [_line_item_values(item) for item in document.line_items],
        "audit": audit_trail_to_dict(audit_trail),
    }


def export_verified_csv(session: ReviewSession) -> str:
    """Return a deterministic flat CSV with one row per verified line item."""
    _require_verified_session(session)

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for item in session.document.line_items:
        writer.writerow(_csv_row(session.document, item))
    return output.getvalue()


def _require_verified_session(session: ReviewSession) -> None:
    if session.status is not ReviewStatus.VERIFIED:
        raise ExportNotAllowedError("document must be explicitly VERIFIED before export")
    if session.validation_result.decision is not ValidationDecision.PASS:
        raise ExportNotAllowedError("VERIFIED export requires a PASS validation decision")

    fresh_validation = validate_document(session.document)
    if fresh_validation != session.validation_result:
        raise ExportIntegrityError("verified session validation is stale or inconsistent")


def _require_matching_audit(session: ReviewSession, audit_trail: AuditTrail) -> None:
    if not audit_trail.document_id:
        raise AuditExportMismatchError("audit trail document_id must not be empty")
    if not audit_trail.events:
        raise AuditExportMismatchError("audit trail must end with DOCUMENT_VERIFIED")

    final_event = audit_trail.events[-1]
    if final_event.event_type is not AuditEventType.DOCUMENT_VERIFIED:
        raise AuditExportMismatchError("audit trail must end with DOCUMENT_VERIFIED")
    if final_event.revision != session.revision:
        raise AuditExportMismatchError("audit revision does not match verified session revision")


def _document_values(document: NormalizedDocument) -> dict[str, object]:
    return {
        "document_number": _export_value(document.document_number.value),
        "document_date": _export_value(document.document_date.value),
        "supplier_name": _export_value(document.supplier_name.value),
        "supplier_tax_id": _export_value(document.supplier_tax_id.value),
        "buyer_name": _export_value(document.buyer_name.value),
        "buyer_tax_id": _export_value(document.buyer_tax_id.value),
        "currency": _export_value(document.currency.value),
        "responsible_person": _export_value(document.responsible_person.value),
        "subtotal": _export_value(document.subtotal.value),
        "vat_total": _export_value(document.vat_total.value),
        "grand_total": _export_value(document.grand_total.value),
    }


def _line_item_values(item: NormalizedLineItem) -> dict[str, object]:
    return {
        "line_number": _export_value(item.line_number.value),
        "product_description": _export_value(item.product_description.value),
        "sku": _export_value(item.sku.value),
        "barcode": _export_value(item.barcode.value),
        "unit": _export_value(item.unit.value),
        "quantity": _export_value(item.quantity.value),
        "unit_price": _export_value(item.unit_price.value),
        "vat_amount": _export_value(item.vat_amount.value),
        "line_total": _export_value(item.line_total.value),
    }


def _csv_row(document: NormalizedDocument, item: NormalizedLineItem) -> dict[str, str]:
    return {
        "document_number": _csv_value(document.document_number.value),
        "document_date": _csv_value(document.document_date.value),
        "supplier_name": _csv_value(document.supplier_name.value),
        "supplier_tax_id": _csv_value(document.supplier_tax_id.value),
        "buyer_name": _csv_value(document.buyer_name.value),
        "buyer_tax_id": _csv_value(document.buyer_tax_id.value),
        "currency": _csv_value(document.currency.value),
        "responsible_person": _csv_value(document.responsible_person.value),
        "line_number": _csv_value(item.line_number.value),
        "sku": _csv_value(item.sku.value),
        "barcode": _csv_value(item.barcode.value),
        "product_description": _csv_value(item.product_description.value),
        "unit": _csv_value(item.unit.value),
        "quantity": _csv_value(item.quantity.value),
        "unit_price": _csv_value(item.unit_price.value),
        "vat_amount": _csv_value(item.vat_amount.value),
        "line_total": _csv_value(item.line_total.value),
        "document_subtotal": _csv_value(document.subtotal.value),
        "document_vat_total": _csv_value(document.vat_total.value),
        "document_grand_total": _csv_value(document.grand_total.value),
    }


def _export_value(value: object) -> object:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExportIntegrityError("non-finite Decimal cannot be exported")
        return format(value, "f")
    raise ExportIntegrityError("unsupported normalized value type")


def _csv_value(value: object) -> str:
    exported = _export_value(value)
    return "" if exported is None else exported

"""In-memory orchestration for the credential-free DocFlow demo workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from threading import RLock
from uuid import UUID

from docflow.audit import (
    AuditTrail,
    audit_trail_to_dict,
    create_audit_trail,
    record_correction_applied,
    record_document_approved,
    record_document_ingested,
    record_document_verified,
    record_extraction_completed,
    record_review_started,
    record_validation_completed,
)
from docflow.export import export_verified_csv, export_verified_json
from docflow.models import IngestionMetadata, NormalizedDocument, NormalizedLineItem
from docflow.normalization import normalize_document
from docflow.review import (
    ApprovalNotAllowedError,
    ReviewSession,
    ReviewStatus,
    apply_correction,
    approve_review,
    start_review,
)
from docflow.validation import ValidationIssue, validate_document

DEMO_DOCUMENT_ID = "demo-sintech-form-z2-139"
DEMO_MODE = "fixture"
DEMO_ERROR_FIELD = "line_items[0].line_total"
DEMO_INCORRECT_LINE_TOTAL = "167 981,00"
DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures/nutrient/sintech_extract_response.json"
)


class DemoError(ValueError):
    """Base class for controlled demo-service failures."""


class DemoConfigurationError(DemoError):
    """The checked-in demo fixture is unavailable or malformed."""


class DemoVerificationError(DemoError):
    """The current demo state cannot be explicitly verified."""


@dataclass(frozen=True, slots=True)
class DemoState:
    """One immutable demo lifecycle snapshot."""

    session: ReviewSession
    audit_trail: AuditTrail


class _DemoClock:
    """Monotonic deterministic timestamps for repeatable fixture runs."""

    def __init__(self) -> None:
        self._next = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        return value


class _DemoUUIDFactory:
    """Deterministic valid event identifiers for repeatable fixture runs."""

    def __init__(self) -> None:
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class DemoService:
    """Coordinate one in-memory document through existing DocFlow domain APIs."""

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
        self._fixture_path = fixture_path
        self._lock = RLock()
        self._clock = _DemoClock()
        self._uuid_factory = _DemoUUIDFactory()
        self._state = self._build_initial_state()

    def start(self) -> dict[str, object]:
        """Start a fresh deterministic fixture lifecycle in REVIEW."""
        return self.reset()

    def reset(self) -> dict[str, object]:
        """Replace current state with the intentional REVIEW fixture."""
        with self._lock:
            self._clock = _DemoClock()
            self._uuid_factory = _DemoUUIDFactory()
            self._state = self._build_initial_state()
            return self._serialize_state(self._state)

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-safe representation of the current immutable state."""
        with self._lock:
            return self._serialize_state(self._state)

    def correct(self, field_path: str, raw_value: str) -> dict[str, object]:
        """Apply one correction through the existing review domain function."""
        with self._lock:
            previous = self._state
            session = apply_correction(
                previous.session,
                field_path,
                raw_value,
                clock=self._clock,
            )
            trail = record_correction_applied(
                previous.audit_trail,
                session.corrections[-1],
                uuid_factory=self._uuid_factory,
            )
            trail = record_validation_completed(
                trail,
                session.validation_result,
                revision=session.revision,
                clock=self._clock,
                uuid_factory=self._uuid_factory,
            )
            self._state = DemoState(session=session, audit_trail=trail)
            return self._serialize_state(self._state)

    def verify(self) -> dict[str, object]:
        """Explicitly approve a PASS document and record approval plus VERIFIED."""
        with self._lock:
            previous = self._state
            if previous.session.status is ReviewStatus.VERIFIED:
                raise DemoVerificationError("Document is already verified.")
            try:
                session = approve_review(previous.session, clock=self._clock)
            except ApprovalNotAllowedError as error:
                raise DemoVerificationError(
                    "Resolve all validation issues before verifying the document."
                ) from error
            trail = record_document_approved(
                previous.audit_trail,
                session,
                uuid_factory=self._uuid_factory,
            )
            trail = record_document_verified(
                trail,
                session,
                uuid_factory=self._uuid_factory,
            )
            self._state = DemoState(session=session, audit_trail=trail)
            return self._serialize_state(self._state)

    def export_json(self) -> dict[str, object]:
        """Export the current state through the VERIFIED-only JSON domain API."""
        with self._lock:
            return export_verified_json(self._state.session, self._state.audit_trail)

    def export_csv(self) -> str:
        """Export the current state through the VERIFIED-only CSV domain API."""
        with self._lock:
            return export_verified_csv(self._state.session)

    def _build_initial_state(self) -> DemoState:
        response = self._load_fixture()
        document_payload = _fixture_document_payload(response)
        line_items = document_payload.get("line_items")
        if (
            not isinstance(line_items, list)
            or not line_items
            or not isinstance(line_items[0], dict)
        ):
            raise DemoConfigurationError("Demo fixture must contain at least one line item.")
        line_items[0]["line_total"] = DEMO_INCORRECT_LINE_TOTAL

        document = normalize_document(document_payload)
        validation = validate_document(document)
        metadata = _fixture_metadata(self._fixture_path, response, self._clock())
        trail = create_audit_trail(metadata.document_id)
        trail = record_document_ingested(trail, metadata, uuid_factory=self._uuid_factory)
        trail = record_extraction_completed(
            trail,
            metadata,
            clock=self._clock,
            uuid_factory=self._uuid_factory,
        )
        trail = record_validation_completed(
            trail,
            validation,
            revision=0,
            clock=self._clock,
            uuid_factory=self._uuid_factory,
        )
        session = start_review(document, validation, clock=self._clock)
        trail = record_review_started(trail, session, uuid_factory=self._uuid_factory)
        return DemoState(session=session, audit_trail=trail)

    def _load_fixture(self) -> dict[str, object]:
        try:
            payload = json.loads(self._fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DemoConfigurationError("Demo fixture could not be loaded.") from error
        if not isinstance(payload, dict):
            raise DemoConfigurationError("Demo fixture root must be an object.")
        return payload

    def _serialize_state(self, state: DemoState) -> dict[str, object]:
        session = state.session
        return {
            "document_id": state.audit_trail.document_id,
            "mode": DEMO_MODE,
            "status": session.status.value,
            "revision": session.revision,
            "can_verify": session.status is ReviewStatus.PASS,
            "can_export": session.status is ReviewStatus.VERIFIED,
            "document": _document_to_dict(session.document),
            "line_items": [
                _line_item_to_dict(item, index)
                for index, item in enumerate(session.document.line_items)
            ],
            "validation": {
                "decision": session.validation_result.decision.value,
                "issue_count": len(session.validation_result.issues),
                "issues": [_issue_to_dict(issue) for issue in session.validation_result.issues],
            },
            "audit": audit_trail_to_dict(state.audit_trail),
        }


def _fixture_document_payload(response: dict[str, object]) -> dict[str, object]:
    try:
        output = response["output"]
        if not isinstance(output, dict):
            raise TypeError
        data = output["data"]
        if not isinstance(data, dict):
            raise TypeError
    except (KeyError, TypeError) as error:
        raise DemoConfigurationError("Demo fixture is missing output.data.") from error
    # JSON round-tripping gives this demo its own mutable copy without touching the fixture object.
    copied = json.loads(json.dumps(data, ensure_ascii=False))
    if not isinstance(copied, dict):
        raise DemoConfigurationError("Demo fixture output.data must be an object.")
    return copied


def _fixture_metadata(
    fixture_path: Path,
    response: dict[str, object],
    ingested_at: datetime,
) -> IngestionMetadata:
    request_id = response.get("requestId")
    status = response.get("status")
    return IngestionMetadata(
        document_id=DEMO_DOCUMENT_ID,
        original_filename=fixture_path.name,
        mime_type="application/json",
        file_size_bytes=fixture_path.stat().st_size,
        ingested_at=ingested_at,
        provider="nutrient-demo-fixture",
        provider_status=status if isinstance(status, int) else 200,
        raw_response_path=fixture_path,
        provider_request_id=request_id if isinstance(request_id, str) else None,
    )


def _document_to_dict(document: NormalizedDocument) -> dict[str, object]:
    return {
        "document_number": _json_value(document.document_number.value),
        "document_date": _json_value(document.document_date.value),
        "supplier_name": _json_value(document.supplier_name.value),
        "supplier_tax_id": _json_value(document.supplier_tax_id.value),
        "buyer_name": _json_value(document.buyer_name.value),
        "buyer_tax_id": _json_value(document.buyer_tax_id.value),
        "currency": _json_value(document.currency.value),
        "responsible_person": _json_value(document.responsible_person.value),
        "subtotal": _json_value(document.subtotal.value),
        "vat_total": _json_value(document.vat_total.value),
        "grand_total": _json_value(document.grand_total.value),
    }


def _line_item_to_dict(item: NormalizedLineItem, index: int) -> dict[str, object]:
    return {
        "source_index": index,
        "line_number": _json_value(item.line_number.value),
        "product_description": _json_value(item.product_description.value),
        "sku": _json_value(item.sku.value),
        "barcode": _json_value(item.barcode.value),
        "unit": _json_value(item.unit.value),
        "quantity": _json_value(item.quantity.value),
        "unit_price": _json_value(item.unit_price.value),
        "vat_amount": _json_value(item.vat_amount.value),
        "line_total": _json_value(item.line_total.value),
        "line_total_raw": _json_value(item.line_total.raw_value),
    }


def _issue_to_dict(issue: ValidationIssue) -> dict[str, object]:
    return {
        "reason_code": issue.reason_code.value,
        "field_path": issue.field_path,
        "message": issue.message,
        "severity": issue.severity.value,
        "expected": _json_value(issue.expected),
        "actual": _json_value(issue.actual),
    }


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise DemoConfigurationError(f"Unsupported demo serialization type: {type(value).__name__}")

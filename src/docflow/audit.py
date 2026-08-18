"""Immutable, append-only audit events for the DocFlow document lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID, uuid4

from docflow.models import IngestionMetadata
from docflow.review import ReviewCorrection, ReviewSession, ReviewStatus
from docflow.validation import ValidationDecision, ValidationReasonCode, ValidationResult

Clock = Callable[[], datetime]
UUIDFactory = Callable[[], UUID]


class AuditEventType(StrEnum):
    """Minimal explicit lifecycle events recorded by Audit Trail v1."""

    DOCUMENT_INGESTED = "DOCUMENT_INGESTED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    REVIEW_STARTED = "REVIEW_STARTED"
    CORRECTION_APPLIED = "CORRECTION_APPLIED"
    DOCUMENT_APPROVED = "DOCUMENT_APPROVED"
    DOCUMENT_VERIFIED = "DOCUMENT_VERIFIED"


class AuditError(ValueError):
    """Base class for controlled audit-domain failures."""


class AuditDocumentMismatchError(AuditError):
    """An event belongs to a different document than its audit trail."""


class AuditTimestampError(AuditError):
    """An audit timestamp is missing timezone information."""


class AuditSequenceError(AuditError):
    """An audit event sequence is invalid or non-contiguous."""


class AuditRevisionError(AuditError):
    """An audit revision is invalid or moves correction history backwards."""


class AuditPayloadError(AuditError):
    """An event payload is invalid for immutable audit storage."""


@dataclass(frozen=True, slots=True)
class IngestionAuditData:
    provider: str
    original_filename: str
    mime_type: str
    file_size_bytes: int
    provider_status: int
    provider_request_id: str | None


@dataclass(frozen=True, slots=True)
class ExtractionAuditData:
    provider: str
    provider_status: int
    provider_request_id: str | None


@dataclass(frozen=True, slots=True)
class ValidationAuditData:
    decision: ValidationDecision
    issue_count: int
    reason_codes: tuple[ValidationReasonCode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ValidationDecision):
            raise AuditPayloadError("validation decision must be a ValidationDecision")
        if not _is_integer(self.issue_count) or self.issue_count < 0:
            raise AuditPayloadError("validation issue_count must be non-negative")
        if not isinstance(self.reason_codes, tuple):
            raise AuditPayloadError("validation reason_codes must be an immutable tuple")
        if not all(isinstance(code, ValidationReasonCode) for code in self.reason_codes):
            raise AuditPayloadError("validation reason_codes contain an invalid value")
        if self.issue_count != len(self.reason_codes):
            raise AuditPayloadError("validation issue_count must match reason_codes")


@dataclass(frozen=True, slots=True)
class ReviewStartedAuditData:
    status: ReviewStatus
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReviewStatus):
            raise AuditPayloadError("review status must be a ReviewStatus")
        _validate_revision(self.revision)


@dataclass(frozen=True, slots=True)
class CorrectionAuditData:
    field_path: str
    old_raw_value: object
    new_raw_value: object
    revision: int
    corrected_at: datetime

    def __post_init__(self) -> None:
        if not self.field_path:
            raise AuditPayloadError("correction field_path must not be empty")
        _validate_immutable_raw(self.old_raw_value, "old_raw_value")
        _validate_immutable_raw(self.new_raw_value, "new_raw_value")
        _validate_revision(self.revision)
        object.__setattr__(self, "corrected_at", _to_utc(self.corrected_at))


@dataclass(frozen=True, slots=True)
class ApprovalAuditData:
    validation_decision: ValidationDecision
    status: ReviewStatus
    revision: int

    def __post_init__(self) -> None:
        if self.validation_decision is not ValidationDecision.PASS:
            raise AuditPayloadError("approval data requires PASS validation")
        if self.status is not ReviewStatus.VERIFIED:
            raise AuditPayloadError("approval data requires VERIFIED status")
        _validate_revision(self.revision)


@dataclass(frozen=True, slots=True)
class VerifiedAuditData:
    status: ReviewStatus
    revision: int

    def __post_init__(self) -> None:
        if self.status is not ReviewStatus.VERIFIED:
            raise AuditPayloadError("verified data requires VERIFIED status")
        _validate_revision(self.revision)


AuditData: TypeAlias = (
    IngestionAuditData
    | ExtractionAuditData
    | ValidationAuditData
    | ReviewStartedAuditData
    | CorrectionAuditData
    | ApprovalAuditData
    | VerifiedAuditData
)

_PAYLOAD_TYPES: dict[AuditEventType, type[AuditData]] = {
    AuditEventType.DOCUMENT_INGESTED: IngestionAuditData,
    AuditEventType.EXTRACTION_COMPLETED: ExtractionAuditData,
    AuditEventType.VALIDATION_COMPLETED: ValidationAuditData,
    AuditEventType.REVIEW_STARTED: ReviewStartedAuditData,
    AuditEventType.CORRECTION_APPLIED: CorrectionAuditData,
    AuditEventType.DOCUMENT_APPROVED: ApprovalAuditData,
    AuditEventType.DOCUMENT_VERIFIED: VerifiedAuditData,
}


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable, document-scoped lifecycle fact."""

    event_id: str
    document_id: str
    event_type: AuditEventType
    timestamp: datetime
    sequence: int
    revision: int
    data: AuditData

    def __post_init__(self) -> None:
        _validate_document_id(self.document_id)
        _validate_event_id(self.event_id)
        if not isinstance(self.event_type, AuditEventType):
            raise AuditPayloadError("event_type must be an AuditEventType")
        if not _is_integer(self.sequence) or self.sequence < 1:
            raise AuditSequenceError("audit event sequence must be a positive integer")
        _validate_revision(self.revision)
        expected_payload = _PAYLOAD_TYPES.get(self.event_type)
        if expected_payload is None or not isinstance(self.data, expected_payload):
            payload_name = expected_payload.__name__ if expected_payload else "known"
            raise AuditPayloadError(f"{self.event_type.value} requires {payload_name} data")
        payload_revision = getattr(self.data, "revision", self.revision)
        if payload_revision != self.revision:
            raise AuditRevisionError("event revision must match its payload revision")
        object.__setattr__(self, "timestamp", _to_utc(self.timestamp))


@dataclass(frozen=True, slots=True)
class AuditTrail:
    """One document's immutable, contiguous sequence of audit events."""

    document_id: str
    events: tuple[AuditEvent, ...]

    def __post_init__(self) -> None:
        _validate_document_id(self.document_id)
        if not isinstance(self.events, tuple):
            raise AuditPayloadError("audit trail events must be an immutable tuple")

        last_correction_revision = 0
        for expected_sequence, event in enumerate(self.events, start=1):
            if event.document_id != self.document_id:
                raise AuditDocumentMismatchError(
                    "all audit events must match the trail document_id"
                )
            if event.sequence != expected_sequence:
                raise AuditSequenceError("audit event sequences must be contiguous from 1")
            if event.event_type is AuditEventType.CORRECTION_APPLIED:
                if event.revision <= last_correction_revision:
                    raise AuditRevisionError(
                        "correction revisions must increase strictly in append order"
                    )
                last_correction_revision = event.revision


def create_audit_trail(document_id: str) -> AuditTrail:
    """Create an empty immutable trail for one ingestion document ID."""
    return AuditTrail(document_id=document_id, events=())


def append_event(trail: AuditTrail, event: AuditEvent) -> AuditTrail:
    """Append exactly one valid next event and return a new audit trail."""
    if event.document_id != trail.document_id:
        raise AuditDocumentMismatchError("event document_id does not match audit trail")
    expected_sequence = len(trail.events) + 1
    if event.sequence != expected_sequence:
        raise AuditSequenceError(
            f"expected audit sequence {expected_sequence}, received {event.sequence}"
        )
    return AuditTrail(document_id=trail.document_id, events=(*trail.events, event))


def record_document_ingested(
    trail: AuditTrail,
    metadata: IngestionMetadata,
    *,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record safe ingestion metadata without file bytes or provider responses."""
    data = IngestionAuditData(
        provider=metadata.provider,
        original_filename=metadata.original_filename,
        mime_type=metadata.mime_type,
        file_size_bytes=metadata.file_size_bytes,
        provider_status=metadata.provider_status,
        provider_request_id=metadata.provider_request_id,
    )
    return _record(
        trail,
        document_id=metadata.document_id,
        event_type=AuditEventType.DOCUMENT_INGESTED,
        timestamp=metadata.ingested_at,
        revision=0,
        data=data,
        uuid_factory=uuid_factory,
    )


def record_extraction_completed(
    trail: AuditTrail,
    metadata: IngestionMetadata,
    *,
    clock: Clock,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record provider completion without copying raw extraction output."""
    data = ExtractionAuditData(
        provider=metadata.provider,
        provider_status=metadata.provider_status,
        provider_request_id=metadata.provider_request_id,
    )
    return _record(
        trail,
        document_id=metadata.document_id,
        event_type=AuditEventType.EXTRACTION_COMPLETED,
        timestamp=clock(),
        revision=0,
        data=data,
        uuid_factory=uuid_factory,
    )


def record_validation_completed(
    trail: AuditTrail,
    validation_result: ValidationResult,
    *,
    revision: int,
    clock: Clock,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record an established validation result without re-running validation."""
    data = ValidationAuditData(
        decision=validation_result.decision,
        issue_count=len(validation_result.issues),
        reason_codes=tuple(issue.reason_code for issue in validation_result.issues),
    )
    return _record(
        trail,
        document_id=trail.document_id,
        event_type=AuditEventType.VALIDATION_COMPLETED,
        timestamp=clock(),
        revision=revision,
        data=data,
        uuid_factory=uuid_factory,
    )


def record_review_started(
    trail: AuditTrail,
    session: ReviewSession,
    *,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record the already-established initial review status and revision."""
    data = ReviewStartedAuditData(status=session.status, revision=session.revision)
    return _record(
        trail,
        document_id=trail.document_id,
        event_type=AuditEventType.REVIEW_STARTED,
        timestamp=session.created_at,
        revision=session.revision,
        data=data,
        uuid_factory=uuid_factory,
    )


def record_correction_applied(
    trail: AuditTrail,
    correction: ReviewCorrection,
    *,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record one accepted review correction with exact raw values."""
    data = CorrectionAuditData(
        field_path=correction.field_path,
        old_raw_value=correction.old_raw_value,
        new_raw_value=correction.new_raw_value,
        revision=correction.revision,
        corrected_at=correction.corrected_at,
    )
    return _record(
        trail,
        document_id=trail.document_id,
        event_type=AuditEventType.CORRECTION_APPLIED,
        timestamp=correction.corrected_at,
        revision=correction.revision,
        data=data,
        uuid_factory=uuid_factory,
    )


def record_document_approved(
    trail: AuditTrail,
    session: ReviewSession,
    *,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record the explicit approval fact from an already verified session."""
    _require_verified_session(session)
    data = ApprovalAuditData(
        validation_decision=session.validation_result.decision,
        status=session.status,
        revision=session.revision,
    )
    return _record(
        trail,
        document_id=trail.document_id,
        event_type=AuditEventType.DOCUMENT_APPROVED,
        timestamp=session.updated_at,
        revision=session.revision,
        data=data,
        uuid_factory=uuid_factory,
    )


def record_document_verified(
    trail: AuditTrail,
    session: ReviewSession,
    *,
    uuid_factory: UUIDFactory = uuid4,
) -> AuditTrail:
    """Record VERIFIED as the explicit resulting document state."""
    _require_verified_session(session)
    data = VerifiedAuditData(status=session.status, revision=session.revision)
    return _record(
        trail,
        document_id=trail.document_id,
        event_type=AuditEventType.DOCUMENT_VERIFIED,
        timestamp=session.updated_at,
        revision=session.revision,
        data=data,
        uuid_factory=uuid_factory,
    )


def audit_trail_to_dict(trail: AuditTrail) -> dict[str, object]:
    """Return a JSON-safe in-memory representation without writing any files."""
    return {
        "document_id": trail.document_id,
        "events": [
            {
                "event_id": event.event_id,
                "document_id": event.document_id,
                "event_type": event.event_type.value,
                "timestamp": _serialize(event.timestamp),
                "sequence": event.sequence,
                "revision": event.revision,
                "data": _serialize(event.data),
            }
            for event in trail.events
        ],
    }


def _record(
    trail: AuditTrail,
    *,
    document_id: str,
    event_type: AuditEventType,
    timestamp: datetime,
    revision: int,
    data: AuditData,
    uuid_factory: UUIDFactory,
) -> AuditTrail:
    event = AuditEvent(
        event_id=str(uuid_factory()),
        document_id=document_id,
        event_type=event_type,
        timestamp=timestamp,
        sequence=len(trail.events) + 1,
        revision=revision,
        data=data,
    )
    return append_event(trail, event)


def _require_verified_session(session: ReviewSession) -> None:
    if session.status is not ReviewStatus.VERIFIED:
        raise AuditPayloadError("approval audit requires VERIFIED review status")
    if session.validation_result.decision is not ValidationDecision.PASS:
        raise AuditPayloadError("approval audit requires PASS validation decision")


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id:
        raise AuditPayloadError("document_id must be a non-empty string")


def _validate_event_id(event_id: str) -> None:
    try:
        UUID(event_id)
    except (ValueError, TypeError, AttributeError):
        raise AuditPayloadError("event_id must be a UUID string") from None


def _validate_revision(revision: int) -> None:
    if not _is_integer(revision) or revision < 0:
        raise AuditRevisionError("audit revision must be a non-negative integer")


def _validate_immutable_raw(raw_value: object, field_name: str) -> None:
    if raw_value is None or isinstance(raw_value, (str, int, bool, Decimal)):
        return
    raise AuditPayloadError(f"{field_name} must be an immutable audit scalar")


def _to_utc(timestamp: datetime) -> datetime:
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise AuditTimestampError("audit timestamps must be timezone-aware")
    if timestamp.utcoffset() is None:
        raise AuditTimestampError("audit timestamps must have a valid UTC offset")
    return timestamp.astimezone(UTC)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _serialize(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return _to_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise AuditPayloadError(f"unsupported audit serialization type: {type(value).__name__}")

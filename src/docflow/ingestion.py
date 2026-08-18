"""Local document ingestion boundary for DocFlow AI."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4

from docflow.models import IngestionMetadata, IngestionResult
from docflow.nutrient import NutrientClient
from docflow.schemas import FORM_Z2_EXTRACTION_INSTRUCTIONS, FORM_Z2_EXTRACTION_SCHEMA

DEFAULT_MAX_FILE_SIZE_MB = Decimal("10")
DEFAULT_RAW_ARTIFACTS_DIR = Path("artifacts/raw")
PROVIDER_NAME = "nutrient_dws"

_SUPPORTED_TYPES = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
}


class IngestionError(RuntimeError):
    """Base class for controlled ingestion failures."""


class UnsupportedFileTypeError(IngestionError):
    """The filename extension is outside the MVP allowlist."""


class UnsupportedMimeTypeError(IngestionError):
    """The file signature does not match its supported extension."""


class FileTooLargeError(IngestionError):
    """The file exceeds the configured ingestion limit."""


class UnreadableFileError(IngestionError):
    """The local file is missing or cannot be read."""


class IngestionConfigurationError(IngestionError):
    """Required ingestion configuration is absent or invalid."""


class IngestionPersistenceError(IngestionError):
    """The raw provider response could not be saved."""


class IngestionService:
    """Validate, extract, and preserve one local accounting document."""

    def __init__(
        self,
        *,
        nutrient_client: NutrientClient,
        raw_artifacts_dir: Path = DEFAULT_RAW_ARTIFACTS_DIR,
        max_file_size_bytes: int | None = None,
        environ: Mapping[str, str] | None = None,
        extraction_schema: Mapping[str, object] = FORM_Z2_EXTRACTION_SCHEMA,
        extraction_instructions: str | None = FORM_Z2_EXTRACTION_INSTRUCTIONS,
        extraction_mode: str = "understand",
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._nutrient_client = nutrient_client
        self._raw_artifacts_dir = Path(raw_artifacts_dir)
        self._environ = os.environ if environ is None else environ
        self._max_file_size_bytes = (
            _max_file_size_from_environment(self._environ)
            if max_file_size_bytes is None
            else max_file_size_bytes
        )
        if self._max_file_size_bytes <= 0:
            raise IngestionConfigurationError("maximum file size must be positive")
        self._extraction_schema = deepcopy(dict(extraction_schema))
        self._extraction_instructions = extraction_instructions
        self._extraction_mode = extraction_mode
        self._id_factory = id_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def ingest(self, file_path: str | Path) -> IngestionResult:
        """Ingest one supported local file and persist the exact provider JSON bytes."""
        path = Path(file_path)
        mime_type, file_size = self._validate_file(path)
        api_key = self._environ.get("NUTRIENT_API_KEY", "").strip()
        if not api_key:
            raise IngestionConfigurationError("NUTRIENT_API_KEY is required")

        document_id = str(self._id_factory())
        ingested_at = self._clock()
        provider_response = self._nutrient_client.extract(
            path,
            mime_type=mime_type,
            api_key=api_key,
            schema=self._extraction_schema,
            instructions=self._extraction_instructions,
            mode=self._extraction_mode,
        )
        raw_response_path = self._persist_raw_response(document_id, provider_response.raw_body)

        metadata = IngestionMetadata(
            document_id=document_id,
            original_filename=path.name,
            mime_type=mime_type,
            file_size_bytes=file_size,
            ingested_at=ingested_at,
            provider=PROVIDER_NAME,
            provider_status=provider_response.status_code,
            raw_response_path=raw_response_path,
            provider_request_id=provider_response.request_id,
        )
        return IngestionResult(metadata=metadata, raw_response=provider_response.json_body)

    def _validate_file(self, path: Path) -> tuple[str, int]:
        file_type = _SUPPORTED_TYPES.get(path.suffix.lower())
        if file_type is None:
            raise UnsupportedFileTypeError(
                "supported file extensions are .pdf, .jpg, .jpeg, and .png"
            )
        if not path.is_file():
            raise UnreadableFileError(f"document is missing or unreadable: {path}")

        try:
            file_size = path.stat().st_size
            with path.open("rb") as document:
                signature = document.read(8)
        except OSError:
            raise UnreadableFileError(f"document is missing or unreadable: {path}") from None

        if file_size > self._max_file_size_bytes:
            raise FileTooLargeError(
                f"document size {file_size} exceeds limit {self._max_file_size_bytes} bytes"
            )

        mime_type, expected_signature = file_type
        if not signature.startswith(expected_signature):
            raise UnsupportedMimeTypeError(
                f"file content does not match the {path.suffix.lower()} file type"
            )
        return mime_type, file_size

    def _persist_raw_response(self, document_id: str, raw_body: bytes) -> Path:
        destination_dir = self._raw_artifacts_dir / document_id
        destination = destination_dir / "nutrient_response.json"
        temporary = destination_dir / "nutrient_response.json.tmp"
        try:
            destination_dir.mkdir(parents=True, exist_ok=False)
            temporary.write_bytes(raw_body)
            temporary.replace(destination)
        except OSError:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise IngestionPersistenceError("failed to preserve raw Nutrient response") from None
        return destination


def _max_file_size_from_environment(environ: Mapping[str, str]) -> int:
    raw_value = environ.get("DOCFLOW_MAX_FILE_SIZE_MB", "").strip()
    if not raw_value:
        megabytes = DEFAULT_MAX_FILE_SIZE_MB
    else:
        try:
            megabytes = Decimal(raw_value)
        except InvalidOperation:
            raise IngestionConfigurationError(
                "DOCFLOW_MAX_FILE_SIZE_MB must be a positive number"
            ) from None
    if not megabytes.is_finite() or megabytes <= 0:
        raise IngestionConfigurationError("DOCFLOW_MAX_FILE_SIZE_MB must be a positive number")
    return int(megabytes * 1024 * 1024)

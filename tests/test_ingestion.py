from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from docflow import (
    FileTooLargeError,
    IngestionConfigurationError,
    IngestionService,
    NutrientAPIError,
    NutrientAuthenticationError,
    NutrientClient,
    NutrientInvalidResponseError,
    NutrientNetworkError,
    NutrientTimeoutError,
    UnreadableFileError,
    UnsupportedFileTypeError,
    UnsupportedMimeTypeError,
)

PDF_BYTES = b"%PDF-1.7\nsynthetic test document"
JPG_BYTES = b"\xff\xd8\xff\xe0synthetic test image"
PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic test image"
SUCCESS_BODY = (
    b'{ "status": 200, "requestId": "req-success", "output": {"elements": [{"text": "raw"}]} }\n'
)

Handler = Callable[[httpx.Request], httpx.Response]
ServiceFactory = Callable[..., IngestionService]


@pytest.fixture
def service_factory(tmp_path: Path) -> Iterator[ServiceFactory]:
    clients: list[httpx.Client] = []

    def make(
        handler: Handler,
        *,
        environ: dict[str, str] | None = None,
        max_file_size_bytes: int = 1024,
    ) -> IngestionService:
        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(http_client)
        return IngestionService(
            nutrient_client=NutrientClient(http_client=http_client, timeout_seconds=5),
            raw_artifacts_dir=tmp_path / "raw",
            max_file_size_bytes=max_file_size_bytes,
            environ={"NUTRIENT_API_KEY": "test-api-key"} if environ is None else environ,
        )

    yield make
    for client in clients:
        client.close()


def _write(tmp_path: Path, filename: str, content: bytes) -> Path:
    path = tmp_path / filename
    path.write_bytes(content)
    return path


def _success(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=SUCCESS_BODY)


@pytest.mark.parametrize(
    ("filename", "content", "mime_type"),
    [
        ("invoice.pdf", PDF_BYTES, "application/pdf"),
        ("invoice.jpg", JPG_BYTES, "image/jpeg"),
        ("invoice.jpeg", JPG_BYTES, "image/jpeg"),
        ("invoice.png", PNG_BYTES, "image/png"),
    ],
)
def test_supported_mvp_files_are_accepted(
    service_factory: ServiceFactory,
    tmp_path: Path,
    filename: str,
    content: bytes,
    mime_type: str,
) -> None:
    document = _write(tmp_path, filename, content)

    result = service_factory(_success).ingest(document)

    assert result.metadata.mime_type == mime_type
    assert result.metadata.original_filename == filename


def test_unsupported_file_extension_is_rejected(
    service_factory: ServiceFactory, tmp_path: Path
) -> None:
    document = _write(tmp_path, "invoice.gif", b"GIF89a")

    with pytest.raises(UnsupportedFileTypeError):
        service_factory(_success).ingest(document)


def test_file_signature_must_match_extension(
    service_factory: ServiceFactory, tmp_path: Path
) -> None:
    document = _write(tmp_path, "invoice.pdf", PNG_BYTES)

    with pytest.raises(UnsupportedMimeTypeError):
        service_factory(_success).ingest(document)


def test_missing_file_is_rejected(service_factory: ServiceFactory, tmp_path: Path) -> None:
    with pytest.raises(UnreadableFileError):
        service_factory(_success).ingest(tmp_path / "missing.pdf")


def test_file_size_limit_is_enforced(service_factory: ServiceFactory, tmp_path: Path) -> None:
    document = _write(tmp_path, "large.pdf", PDF_BYTES + b"x" * 100)

    with pytest.raises(FileTooLargeError):
        service_factory(_success, max_file_size_bytes=len(PDF_BYTES)).ingest(document)


def test_success_returns_metadata_and_preserves_exact_raw_json(
    service_factory: ServiceFactory, tmp_path: Path
) -> None:
    document = _write(tmp_path, "original-name.pdf", PDF_BYTES)

    result = service_factory(_success).ingest(document)

    UUID(result.metadata.document_id)
    assert result.metadata.original_filename == "original-name.pdf"
    assert result.metadata.file_size_bytes == len(PDF_BYTES)
    assert result.metadata.provider == "nutrient_dws"
    assert result.metadata.provider_status == 200
    assert result.metadata.provider_request_id == "req-success"
    assert result.metadata.ingested_at.tzinfo is not None
    assert result.metadata.raw_response_path == (
        tmp_path / "raw" / result.metadata.document_id / "nutrient_response.json"
    )
    assert result.metadata.raw_response_path.read_bytes() == SUCCESS_BODY
    assert result.raw_response == {
        "status": 200,
        "requestId": "req-success",
        "output": {"elements": [{"text": "raw"}]},
    }


def test_missing_api_key_is_controlled_and_makes_no_request(
    service_factory: ServiceFactory, tmp_path: Path
) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)

    def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network request must not occur without an API key")

    with pytest.raises(IngestionConfigurationError, match="NUTRIENT_API_KEY"):
        service_factory(unexpected_request, environ={}).ingest(document)


def test_official_parse_request_shape_is_used(
    service_factory: ServiceFactory, tmp_path: Path
) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)

    def inspect_request(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert request.url == "https://api.nutrient.io/extraction/parse"
        assert request.headers["authorization"] == "Bearer test-api-key"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="file"; filename="invoice.pdf"' in body
        assert b"Content-Type: application/pdf" in body
        assert b'name="instructions"' in body
        assert b'{"mode":"understand","output":{"format":"spatial"}}' in body
        return httpx.Response(200, content=SUCCESS_BODY)

    service_factory(inspect_request).ingest(document)


@pytest.mark.parametrize("status_code", [401, 403])
def test_authentication_failures_do_not_expose_api_key(
    service_factory: ServiceFactory, tmp_path: Path, status_code: int
) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)
    secret = "do-not-expose-this-key"

    def auth_failure(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"status": status_code, "requestId": "req-auth", "errorMessage": "denied"},
        )

    with pytest.raises(NutrientAuthenticationError) as error:
        service_factory(auth_failure, environ={"NUTRIENT_API_KEY": secret}).ingest(document)

    assert error.value.status_code == status_code
    assert error.value.request_id == "req-auth"
    assert secret not in str(error.value)


@pytest.mark.parametrize("status_code", [400, 422, 500, 503])
def test_nutrient_http_errors_are_controlled(
    service_factory: ServiceFactory, tmp_path: Path, status_code: int
) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)

    def api_failure(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"status": status_code, "requestId": "req-api", "errorMessage": "failed"},
        )

    with pytest.raises(NutrientAPIError) as error:
        service_factory(api_failure).ingest(document)

    assert error.value.status_code == status_code
    assert error.value.request_id == "req-api"


def test_network_timeout_is_controlled(service_factory: ServiceFactory, tmp_path: Path) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timed out", request=request)

    with pytest.raises(NutrientTimeoutError, match="timed out"):
        service_factory(timeout).ingest(document)


def test_network_failure_is_controlled(service_factory: ServiceFactory, tmp_path: Path) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)

    def network_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    with pytest.raises(NutrientNetworkError, match="network request failed"):
        service_factory(network_failure).ingest(document)


def test_success_with_invalid_json_is_rejected(
    service_factory: ServiceFactory, tmp_path: Path
) -> None:
    document = _write(tmp_path, "invoice.pdf", PDF_BYTES)

    def invalid_json(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"x-request-id": "req-bad"})

    with pytest.raises(NutrientInvalidResponseError) as error:
        service_factory(invalid_json).ingest(document)

    assert error.value.status_code == 200
    assert error.value.request_id == "req-bad"


@pytest.mark.parametrize("configured_value", ["0", "-1", "not-a-number", "Infinity"])
def test_invalid_file_size_configuration_is_rejected(configured_value: str) -> None:
    with pytest.raises(IngestionConfigurationError, match="positive number"):
        IngestionService(
            nutrient_client=NutrientClient(
                http_client=httpx.Client(transport=httpx.MockTransport(_success))
            ),
            environ={
                "NUTRIENT_API_KEY": "test-api-key",
                "DOCFLOW_MAX_FILE_SIZE_MB": configured_value,
            },
        )

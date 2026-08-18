"""Thin HTTP client for Nutrient DWS Data Extraction."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

NUTRIENT_EXTRACT_URL = "https://api.nutrient.io/extraction/extract"


class NutrientError(RuntimeError):
    """Base class for controlled Nutrient failures."""


class NutrientAuthenticationError(NutrientError):
    """Nutrient rejected the supplied credential."""

    def __init__(self, status_code: int, request_id: str | None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        message = _safe_status_message("Nutrient authentication failed", status_code, request_id)
        super().__init__(message)


class NutrientAPIError(NutrientError):
    """Nutrient returned a non-authentication HTTP error."""

    def __init__(self, status_code: int, request_id: str | None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        message = _safe_status_message("Nutrient API request failed", status_code, request_id)
        super().__init__(message)


class NutrientTimeoutError(NutrientError):
    """The Nutrient request exceeded the configured timeout."""


class NutrientNetworkError(NutrientError):
    """The Nutrient request failed before a response was received."""


class NutrientInvalidResponseError(NutrientError):
    """Nutrient returned a successful response that was not valid JSON."""

    def __init__(self, status_code: int, request_id: str | None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(
            _safe_status_message("Nutrient returned malformed JSON", status_code, request_id)
        )


@dataclass(frozen=True, slots=True)
class NutrientResponse:
    """Successful provider response with exact bytes and decoded JSON."""

    status_code: int
    raw_body: bytes
    json_body: object
    request_id: str | None


class NutrientClient:
    """Send one document to the official schema-based Data Extraction endpoint."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
        endpoint: str = NUTRIENT_EXTRACT_URL,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._http_client = http_client
        self._timeout = httpx.Timeout(timeout_seconds)
        self._endpoint = endpoint

    def extract(
        self,
        file_path: Path,
        *,
        mime_type: str,
        api_key: str,
        schema: Mapping[str, object],
        instructions: str | None = None,
        mode: str = "understand",
    ) -> NutrientResponse:
        """Upload a document and return its unmodified response bytes plus parsed JSON."""
        headers = {"Authorization": f"Bearer {api_key}"}
        outer_instructions: dict[str, object] = {
            "schema": schema,
            "parseConfig": {"mode": mode},
        }
        if instructions:
            outer_instructions["instructions"] = instructions
        data = {"instructions": json.dumps(outer_instructions, separators=(",", ":"))}

        try:
            with file_path.open("rb") as document:
                files = {"file": (file_path.name, document, mime_type)}
                if self._http_client is None:
                    with httpx.Client(timeout=self._timeout) as client:
                        response = client.post(
                            self._endpoint,
                            headers=headers,
                            data=data,
                            files=files,
                        )
                else:
                    response = self._http_client.post(
                        self._endpoint,
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=self._timeout,
                    )
        except httpx.TimeoutException:
            raise NutrientTimeoutError("Nutrient request timed out") from None
        except httpx.RequestError:
            raise NutrientNetworkError("Nutrient network request failed") from None

        raw_body = response.content
        request_id = _request_id(response, raw_body)
        if response.status_code in {401, 403}:
            raise NutrientAuthenticationError(response.status_code, request_id)
        if not 200 <= response.status_code < 300:
            raise NutrientAPIError(response.status_code, request_id)

        try:
            json_body = response.json()
        except ValueError:
            raise NutrientInvalidResponseError(response.status_code, request_id) from None

        return NutrientResponse(
            status_code=response.status_code,
            raw_body=raw_body,
            json_body=json_body,
            request_id=request_id,
        )


def _request_id(response: httpx.Response, raw_body: bytes) -> str | None:
    for header in ("x-request-id", "x-nutrient-request-id"):
        if value := response.headers.get(header):
            return value
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(body, dict) and isinstance(body.get("requestId"), str):
        return body["requestId"]
    return None


def _safe_status_message(prefix: str, status_code: int, request_id: str | None) -> str:
    suffix = f"; request_id={request_id}" if request_id else ""
    return f"{prefix} (HTTP {status_code}){suffix}"

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import docflow.demo as demo_module
from docflow.api import create_app
from docflow.demo import DemoService

CORRECTION = {
    "field_path": "line_items[0].line_total",
    "raw_value": "167 881,00",
}


@pytest.fixture
def demo() -> Iterator[tuple[TestClient, DemoService]]:
    service = DemoService()
    with TestClient(create_app(service)) as client:
        yield client, service


def _start(client: TestClient) -> dict[str, object]:
    response = client.post("/api/demo/start")
    assert response.status_code == 200
    return response.json()


def _correct(client: TestClient) -> dict[str, object]:
    response = client.post("/api/demo/correct", json=CORRECTION)
    assert response.status_code == 200
    return response.json()


def _verify(client: TestClient) -> dict[str, object]:
    response = client.post("/api/demo/verify")
    assert response.status_code == 200
    return response.json()


def _issue_codes(state: dict[str, object]) -> list[str]:
    validation = state["validation"]
    assert isinstance(validation, dict)
    issues = validation["issues"]
    assert isinstance(issues, list)
    return [issue["reason_code"] for issue in issues]


def test_demo_start_returns_review(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo

    state = _start(client)

    assert state["status"] == "REVIEW"
    assert state["validation"]["decision"] == "REVIEW"
    assert state["validation"]["issue_count"] == 2


def test_initial_issues_include_line_total_mismatch(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo

    state = _start(client)

    assert "LINE_TOTAL_MISMATCH" in _issue_codes(state)


def test_initial_issues_include_grand_total_mismatch(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo

    state = _start(client)

    assert "GRAND_TOTAL_MISMATCH" in _issue_codes(state)


def test_correction_endpoint_uses_domain_apply_correction(
    demo: tuple[TestClient, DemoService], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = demo
    original = demo_module.apply_correction
    calls: list[tuple[str, object]] = []

    def tracked_apply(session, field_path, raw_value, *, clock=None):
        calls.append((field_path, raw_value))
        return original(session, field_path, raw_value, clock=clock)

    monkeypatch.setattr(demo_module, "apply_correction", tracked_apply)

    _correct(client)

    assert calls == [("line_items[0].line_total", "167 881,00")]


def test_valid_correction_returns_pass(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo

    state = _correct(client)

    assert state["status"] == "PASS"
    assert state["validation"] == {"decision": "PASS", "issue_count": 0, "issues": []}


def test_correction_increments_revision(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo
    before = client.get("/api/demo/state").json()

    after = _correct(client)

    assert before["revision"] == 0
    assert after["revision"] == 1


def test_pass_is_not_verified(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo

    state = _correct(client)

    assert state["status"] == "PASS"
    assert state["can_verify"] is True
    assert state["can_export"] is False


def test_verify_returns_verified(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo
    _correct(client)

    state = _verify(client)

    assert state["status"] == "VERIFIED"
    assert state["can_verify"] is False
    assert state["can_export"] is True


def test_verify_before_pass_is_rejected(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo

    response = client.post("/api/demo/verify")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Resolve all validation issues before verifying the document."
    }


def test_json_export_is_blocked_before_verified(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo

    response = client.get("/api/demo/export/json")

    assert response.status_code == 409
    assert response.json() == {"detail": "The document is not ready for export."}


def test_csv_export_is_blocked_before_verified(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo

    response = client.get("/api/demo/export/csv")

    assert response.status_code == 409
    assert response.json() == {"detail": "The document is not ready for export."}


def test_json_export_after_verified_succeeds(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo
    _correct(client)
    _verify(client)

    response = client.get("/api/demo/export/json")

    assert response.status_code == 200
    assert response.json()["status"] == "VERIFIED"
    assert response.json()["document"]["grand_total"] == "167881.00"


def test_csv_export_after_verified_succeeds(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo
    _correct(client)
    _verify(client)

    response = client.get("/api/demo/export/csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "document_number,document_date,supplier_name" in response.text
    assert "167881.00" in response.text


def test_audit_contains_correction_event(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo

    state = _correct(client)
    events = state["audit"]["events"]
    correction = next(event for event in events if event["event_type"] == "CORRECTION_APPLIED")

    assert correction["revision"] == 1
    assert correction["data"]["field_path"] == "line_items[0].line_total"
    assert correction["data"]["old_raw_value"] == "167 981,00"
    assert correction["data"]["new_raw_value"] == "167 881,00"


def test_final_audit_event_is_document_verified(
    demo: tuple[TestClient, DemoService],
) -> None:
    client, _ = demo
    _correct(client)

    state = _verify(client)
    events = state["audit"]["events"]

    assert [event["event_type"] for event in events] == [
        "DOCUMENT_INGESTED",
        "EXTRACTION_COMPLETED",
        "VALIDATION_COMPLETED",
        "REVIEW_STARTED",
        "CORRECTION_APPLIED",
        "VALIDATION_COMPLETED",
        "DOCUMENT_APPROVED",
        "DOCUMENT_VERIFIED",
    ]
    assert events[-1]["revision"] == state["revision"] == 1


def test_reset_returns_review_state(demo: tuple[TestClient, DemoService]) -> None:
    client, _ = demo
    _correct(client)
    _verify(client)

    response = client.post("/api/demo/reset")

    assert response.status_code == 200
    state = response.json()
    assert state["status"] == "REVIEW"
    assert state["revision"] == 0
    assert len(state["audit"]["events"]) == 4
    assert _issue_codes(state) == ["LINE_TOTAL_MISMATCH", "GRAND_TOTAL_MISMATCH"]


def test_demo_orchestration_delegates_validation_to_domain(
    demo: tuple[TestClient, DemoService], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = demo
    original = demo_module.validate_document
    calls = 0

    def tracked_validate(document):
        nonlocal calls
        calls += 1
        return original(document)

    monkeypatch.setattr(demo_module, "validate_document", tracked_validate)

    response = client.post("/api/demo/reset")

    assert response.status_code == 200
    assert calls == 1

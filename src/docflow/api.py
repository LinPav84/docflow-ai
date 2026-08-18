"""Minimal FastAPI bridge for the DocFlow Demo UI."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from docflow.demo import DemoError, DemoService
from docflow.export import ExportError
from docflow.review import ReviewError


class CorrectionRequest(BaseModel):
    """One raw correction submitted by the review workspace."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    raw_value: str


def create_app(service: DemoService | None = None) -> FastAPI:
    """Create an isolated API app, optionally with a test-owned service."""
    demo_service = service or DemoService()
    application = FastAPI(
        title="DocFlow AI Demo API",
        version="1.0.0",
        description="In-memory bridge to the existing deterministic DocFlow domain.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "fixture"}

    @application.post("/api/demo/start")
    def start_demo() -> dict[str, object]:
        return demo_service.start()

    @application.post("/api/demo/reset")
    def reset_demo() -> dict[str, object]:
        return demo_service.reset()

    @application.get("/api/demo/state")
    def demo_state() -> dict[str, object]:
        return demo_service.snapshot()

    @application.post("/api/demo/correct")
    def correct_demo(request: CorrectionRequest) -> dict[str, object]:
        try:
            return demo_service.correct(request.field_path, request.raw_value)
        except ReviewError as error:
            raise HTTPException(status_code=422, detail=_safe_message(error)) from error

    @application.post("/api/demo/verify")
    def verify_demo() -> dict[str, object]:
        try:
            return demo_service.verify()
        except DemoError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.get("/api/demo/export/json")
    def export_demo_json() -> JSONResponse:
        try:
            return JSONResponse(content=demo_service.export_json())
        except ExportError as error:
            raise HTTPException(status_code=409, detail=_safe_message(error)) from error

    @application.get("/api/demo/export/csv")
    def export_demo_csv() -> Response:
        try:
            csv_text = demo_service.export_csv()
        except ExportError as error:
            raise HTTPException(status_code=409, detail=_safe_message(error)) from error
        return Response(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="docflow-document-139.csv"'},
        )

    return application


def _safe_message(error: Exception) -> str:
    if isinstance(error, ReviewError):
        return "The correction was rejected. Check the field and value, then try again."
    return "The document is not ready for export."


app = create_app()

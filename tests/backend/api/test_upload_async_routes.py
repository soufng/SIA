"""Tests pour /uploads/analyze/async et /uploads/jobs/{id}."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.v1.routes.uploads import (
    get_analysis_repository,
    get_analysis_service,
    get_jobs_repository,
    get_upload_service,
    router,
)


class FakeUploadService:
    def save_uploaded_file(
        self,
        file_content: bytes,
        original_filename: str,
    ) -> dict[str, str | int]:
        return {
            "original_filename": original_filename,
            "stored_filename": "stored.pdf",
            "file_path": "data/raw/stored.pdf",
            "file_size": len(file_content),
        }


class FakeAnalysisService:
    def analyze_scenario(
        self,
        scenario_id: str,
        file_path: str,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        return {
            "scenario_id": scenario_id,
            "document_stats": {
                "file_name": "stored.pdf",
                "original_filename": original_filename,
                "words_count": 10,
                "chunks_count": 1,
                "file_hash": "h",
                "text_hash": "t",
            },
            "plagiarism": {"global_similarity_score": 0.0},
            "profanity": {},
            "adult_content": {},
            "moroccan_constants": {"risk_level": "faible", "flags": []},
            "rag_report": {"risk_level": "low"},
            "analysis_timestamp": "2026-06-11T00:00:00+00:00",
            "file_hash": "h",
            "text_hash": "t",
        }


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, Any]] = {}

    def save_result(self, result: dict[str, Any]) -> str:
        # Index by scenario_id so find_by_scenario_id can retrieve it.
        sid = (
            result.get("scenario_id")
            or (result.get("result") or {}).get("scenario_id")
            or "unknown"
        )
        self.saved[sid] = result
        return "saved-id"

    def find_by_scenario_id(self, scenario_id: str) -> dict[str, Any] | None:
        return self.saved.get(scenario_id)


class FakeJobsRepository:
    """Stocke les jobs en mémoire, expose ``create_job/update_job/get_job``."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def create_job(
        self,
        *,
        file_path: str,
        original_filename: str,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        doc = {
            "job_id": job_id,
            "scenario_id": scenario_id or str(uuid4()),
            "status": "queued",
            "stage": "queued",
            "progress_pct": 0,
            "original_filename": original_filename,
            "file_path": file_path,
            "result_scenario_id": None,
            "error": None,
            "created_at": "now",
            "updated_at": "now",
        }
        self.jobs[job_id] = doc
        return dict(doc)

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress_pct: int | None = None,
        result_scenario_id: str | None = None,
        error: str | None = None,
    ) -> None:
        doc = self.jobs.get(job_id)
        if doc is None:
            return
        if status is not None:
            doc["status"] = status
        if stage is not None:
            doc["stage"] = stage
        if progress_pct is not None:
            doc["progress_pct"] = progress_pct
        if result_scenario_id is not None:
            doc["result_scenario_id"] = result_scenario_id
        if error is not None:
            doc["error"] = error

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        doc = self.jobs.get(job_id)
        return dict(doc) if doc else None


def build_client(
    upload_service: FakeUploadService | None = None,
    analysis_service: FakeAnalysisService | None = None,
    analysis_repository: FakeAnalysisRepository | None = None,
    jobs_repository: FakeJobsRepository | None = None,
) -> tuple[TestClient, FakeJobsRepository, FakeAnalysisRepository]:
    app = FastAPI()
    app.include_router(router)

    upload_service = upload_service or FakeUploadService()
    analysis_service = analysis_service or FakeAnalysisService()
    analysis_repository = analysis_repository or FakeAnalysisRepository()
    jobs_repository = jobs_repository or FakeJobsRepository()

    app.dependency_overrides[get_upload_service] = lambda: upload_service
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service
    app.dependency_overrides[get_analysis_repository] = (
        lambda: analysis_repository
    )
    app.dependency_overrides[get_jobs_repository] = lambda: jobs_repository

    return TestClient(app), jobs_repository, analysis_repository


def test_async_endpoint_returns_202_and_job_payload() -> None:
    client, jobs_repo, analysis_repo = build_client()

    response = client.post(
        "/uploads/analyze/async",
        files={"file": ("scenario.pdf", b"%PDF-1.4 content", "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "queued"
    assert "job_id" in body
    assert "scenario_id" in body
    # Le BackgroundTask a déjà tourné (TestClient l'exécute synchronement).
    job_after = jobs_repo.get_job(body["job_id"])
    assert job_after is not None
    assert job_after["status"] == "completed"
    # L'analyse a été persistée sous le bon scenario_id.
    assert body["scenario_id"] in analysis_repo.saved


def test_async_endpoint_rejects_missing_file() -> None:
    client, _, _ = build_client()
    response = client.post("/uploads/analyze/async")
    assert response.status_code == 400
    assert response.json()["detail"] == "Un fichier est requis."


def test_async_endpoint_rejects_file_without_pdf_magic() -> None:
    client, _, _ = build_client()
    response = client.post(
        "/uploads/analyze/async",
        files={"file": ("fake.pdf", b"not a pdf at all" * 80, "application/pdf")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_get_job_state_returns_404_for_unknown_job() -> None:
    client, _, _ = build_client()
    response = client.get("/uploads/jobs/does-not-exist")
    assert response.status_code == 404


def test_get_job_state_embeds_analysis_when_completed() -> None:
    client, jobs_repo, analysis_repo = build_client()

    # Crée le job + déclenche l'analyse via le endpoint async.
    ack = client.post(
        "/uploads/analyze/async",
        files={"file": ("scenario.pdf", b"%PDF-1.4 content", "application/pdf")},
    ).json()

    state = client.get(f"/uploads/jobs/{ack['job_id']}")
    assert state.status_code == 200
    payload = state.json()
    assert payload["status"] == "completed"
    assert payload["progress_pct"] == 100
    # L'analyse complète est embarquée dans la réponse pour éviter un
    # second round-trip côté frontend.
    assert payload.get("analysis") is not None
    assert payload["analysis"].get("scenario_id") == ack["scenario_id"]

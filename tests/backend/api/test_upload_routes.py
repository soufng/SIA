from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.v1.routes.uploads import (
    get_analysis_repository,
    get_analysis_service,
    get_upload_service,
    router,
)


class FakeUploadService:
    def __init__(self) -> None:
        self.saved_file: dict[str, Any] | None = None

    def save_uploaded_file(
        self,
        file_content: bytes,
        original_filename: str,
    ) -> dict[str, str | int]:
        self.saved_file = {
            "file_content": file_content,
            "original_filename": original_filename,
        }
        return {
            "original_filename": original_filename,
            "stored_filename": "stored.pdf",
            "file_path": "data/raw/stored.pdf",
            "file_size": len(file_content),
        }


class FakeAnalysisService:
    def __init__(self) -> None:
        self.received_scenario_id: str | None = None
        self.received_file_path: str | None = None

    def analyze_scenario(
        self,
        scenario_id: str,
        file_path: str,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        self.received_scenario_id = scenario_id
        self.received_file_path = file_path
        return {
            "scenario_id": scenario_id,
            "document_stats": {
                "file_name": "stored.pdf",
                "original_filename": original_filename,
                "words_count": 100,
                "chunks_count": 2,
                "file_hash": "file-hash",
                "text_hash": "text-hash",
            },
            "plagiarism": {"score": 0.5, "global_similarity_score": 0.5, "risk": "medium"},
            "profanity": {},
            "adult_content": {},
            "moroccan_constants": {"risk_level": "faible", "flags": []},
            "rag_report": {"risk_level": "medium"},
            "analysis_timestamp": "2026-06-01T00:00:00+00:00",
            "file_hash": "file-hash",
            "text_hash": "text-hash",
        }


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.saved_result: dict[str, Any] | None = None

    def save_result(self, result: dict[str, Any]) -> str:
        self.saved_result = result
        return "saved-analysis-id"


def build_client(
    upload_service: FakeUploadService | None = None,
    analysis_service: FakeAnalysisService | None = None,
    analysis_repository: FakeAnalysisRepository | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    upload_service = upload_service or FakeUploadService()
    analysis_service = analysis_service or FakeAnalysisService()
    analysis_repository = analysis_repository or FakeAnalysisRepository()

    app.dependency_overrides[get_upload_service] = lambda: upload_service
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service
    app.dependency_overrides[get_analysis_repository] = lambda: analysis_repository

    return TestClient(app)


def test_upload_and_analyze_returns_complete_analysis_result() -> None:
    upload_service = FakeUploadService()
    analysis_service = FakeAnalysisService()
    analysis_repository = FakeAnalysisRepository()
    client = build_client(upload_service, analysis_service, analysis_repository)

    response = client.post(
        "/uploads/analyze",
        files={"file": ("scenario.pdf", b"%PDF-1.4 content", "application/pdf")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"]
    assert data["success"] is True
    assert data["analysis"]["scenario_id"] == data["scenario_id"]
    assert data["analysis"]["document_stats"]["file_name"] == "stored.pdf"
    assert data["analysis"]["document_stats"]["original_filename"] == "scenario.pdf"
    assert data["analysis"]["plagiarism"]["score"] == 0.5
    assert data["analysis"]["profanity"] == {}
    assert data["analysis"]["adult_content"] == {}
    assert data["analysis"]["rag_report"] == {"risk_level": "medium"}
    assert upload_service.saved_file == {
        "file_content": b"%PDF-1.4 content",
        "original_filename": "scenario.pdf",
    }
    assert analysis_service.received_file_path == "data/raw/stored.pdf"
    assert analysis_service.received_scenario_id == data["scenario_id"]
    assert analysis_repository.saved_result == {
        "scenario_id": data["scenario_id"],
        "filename": "scenario.pdf",
        "stored_filename": "stored.pdf",
        "file_hash": "file-hash",
        "text_hash": "text-hash",
        "word_count": 100,
        "chunk_count": 2,
        "similarity_score": 0.5,
        "risk_level": "medium",
        "score": 0.5,
        "status": "completed",
        "created_at": "2026-06-01T00:00:00+00:00",
        "analysis_timestamp": "2026-06-01T00:00:00+00:00",
        "warnings": [],
        "moroccan_constants": {"risk_level": "faible", "flags": []},
        "result": data["analysis"],
    }


def test_upload_and_analyze_rejects_non_pdf_file() -> None:
    client = build_client()

    response = client.post(
        "/uploads/analyze",
        files={"file": ("scenario.txt", b"content", "text/plain")},
    )

    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_upload_and_analyze_rejects_missing_file() -> None:
    client = build_client()

    response = client.post("/uploads/analyze")

    assert response.status_code == 400
    assert response.json()["detail"] == "Un fichier est requis."


def test_upload_and_analyze_returns_400_when_upload_validation_fails() -> None:
    class InvalidUploadService(FakeUploadService):
        def save_uploaded_file(
            self,
            file_content: bytes,
            original_filename: str,
        ) -> dict[str, str | int]:
            raise ValueError("storage service is read-only")

    client = build_client(upload_service=InvalidUploadService())

    response = client.post(
        "/uploads/analyze",
        # Has the PDF magic so the route-level validation lets it through;
        # the failure now comes from the service layer.
        files={"file": ("scenario.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "storage service is read-only"


def test_upload_rejects_empty_file_with_clear_french_message() -> None:
    client = build_client()
    response = client.post(
        "/uploads/analyze",
        files={"file": ("scenario.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Le fichier est vide."


def test_upload_rejects_file_without_pdf_magic_header() -> None:
    client = build_client()
    # 1 KiB of garbage bytes with no %PDF- signature in sight.
    payload = b"\x00\x01\x02 not a real pdf at all " * 60
    response = client.post(
        "/uploads/analyze",
        files={"file": ("fake.pdf", payload, "application/pdf")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_upload_rejects_file_exceeding_size_limit(monkeypatch) -> None:
    # Force a tiny limit so we can test without producing a real 20 MiB blob.
    from backend.api.v1.routes import uploads as uploads_module
    monkeypatch.setattr(uploads_module.settings, "UPLOAD_MAX_MB", 1)

    client = build_client()
    # 1 MiB + a few bytes, valid magic header so size is the actual blocker.
    oversized = b"%PDF-1.4\n" + b"a" * (1 * 1024 * 1024 + 16)
    response = client.post(
        "/uploads/analyze",
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413
    assert "Mo" in response.json()["detail"]


def test_upload_and_analyze_returns_500_when_analysis_fails() -> None:
    class FailingAnalysisService(FakeAnalysisService):
        def analyze_scenario(
            self,
            scenario_id: str,
            file_path: str,
            original_filename: str | None = None,
        ) -> dict[str, Any]:
            raise RuntimeError("analysis failed")

    client = build_client(analysis_service=FailingAnalysisService())

    response = client.post(
        "/uploads/analyze",
        files={"file": ("scenario.pdf", b"%PDF-1.4 content", "application/pdf")},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Erreur pendant l'analyse du PDF",
        "error": "analysis failed",
    }

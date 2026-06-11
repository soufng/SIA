"""Tests for the POST /analysis/{scenario_id}/advanced-report endpoint."""

from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.api.v1.routes.analysis import (
    get_advanced_rag_service,
    get_analysis_repository,
)
from backend.main import app
from backend.services.advanced_rag_service import AdvancedRAGService
from backend.services.llm_provider import MockLLMProvider


def _analysis() -> dict[str, Any]:
    return {
        "scenario_id": "scenario-1",
        "document_stats": {
            "original_filename": "demo.pdf",
            "file_name": "abcd.pdf",
            "words_count": 1000,
            "chunks_count": 8,
        },
        "rag_report": {"risk_level": "medium"},
        "plagiarism": {
            "global_similarity_score": 0.5,
            "total_matches": 2,
            "total_sources": 1,
            "matches": [
                {
                    "similarity_score": 0.7,
                    "matched_chunk_text_display": "passage similaire 1",
                    "matched_chunk_text": "passage similaire 1",
                    "chunk_text": "version analysée",
                    "overlap_text": "passage similaire",
                    "matched_scenario_id": "S2",
                    "stored_filename": "s2.pdf",
                    "filename": "s2.pdf",
                    "original_filename": "s2.pdf",
                }
            ],
            "plagiarism_sources": [],
        },
        "profanity": {"profanity_score": 0, "detected_words": []},
        "adult_content": {"adult_content_score": 0, "risk_level": "low"},
    }


def test_advanced_report_with_inline_analysis_payload() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    repo = MagicMock()
    app.dependency_overrides[get_advanced_rag_service] = lambda: service
    app.dependency_overrides[get_analysis_repository] = lambda: repo
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/analysis/scenario-1/advanced-report",
            json={"analysis": _analysis()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scenario_id"] == "scenario-1"
        assert "Synthèse globale" in body["narrative"]
        assert body["llm"]["provider"] == "mock"
        # When the analysis is inlined we must NOT hit MongoDB.
        repo.list_history.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_advanced_report_loads_from_mongo_when_body_empty() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    repo = MagicMock()
    repo.list_history.return_value = [_analysis()]
    app.dependency_overrides[get_advanced_rag_service] = lambda: service
    app.dependency_overrides[get_analysis_repository] = lambda: repo
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/analysis/scenario-1/advanced-report",
            json={},
        )
        assert resp.status_code == 200, resp.text
        repo.list_history.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_advanced_report_404_when_scenario_missing() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    repo = MagicMock()
    repo.list_history.return_value = []
    app.dependency_overrides[get_advanced_rag_service] = lambda: service
    app.dependency_overrides[get_analysis_repository] = lambda: repo
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/analysis/missing-scenario/advanced-report",
            json={},
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_advanced_report_400_for_empty_scenario_id() -> None:
    client = TestClient(app)
    # FastAPI routes don't match empty path segments, so we send a space
    # which the handler rejects explicitly.
    resp = client.post("/api/v1/analysis/%20/advanced-report", json={})
    assert resp.status_code == 400

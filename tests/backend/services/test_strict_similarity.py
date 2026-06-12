"""Tests for the strict-similarity verdict used in the renewal workflow."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.local_similarity_service import LocalSimilarityService
from backend.services.strict_similarity_service import (
    HIGHLY_SIMILAR_THRESHOLD,
    NEAR_IDENTICAL_THRESHOLD,
    StrictSimilarityService,
)


def _history_doc(
    *,
    scenario_id: str = "scenario-prev",
    filename: str = "scenario.pdf",
    file_hash: str | None = None,
    text_hash: str | None = None,
    cleaned_text: str | None = None,
    risk: str = "low",
    timestamp: str = "2026-05-15T10:00:00+00:00",
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "filename": filename,
        "stored_filename": f"{scenario_id}.pdf",
        "file_hash": file_hash,
        "text_hash": text_hash,
        "analysis_timestamp": timestamp,
        "created_at": timestamp,
        "analysis": {
            "scenario_id": scenario_id,
            "rag_report": {"risk_level": risk},
            "cleaned_text": cleaned_text,
            "document_stats": {
                "original_filename": filename,
                "file_name": f"{scenario_id}.pdf",
            },
        },
    }


def _build_service(history: list[dict[str, Any]]) -> StrictSimilarityService:
    repo = MagicMock()
    repo.list_history.return_value = history
    local = LocalSimilarityService(
        raw_dir="/tmp/sia-tests",
        pdf_service=MagicMock(),
        text_cleaning_service=MagicMock(),
        chunking_service=MagicMock(),
        analysis_repository=repo,
    )
    return StrictSimilarityService(
        analysis_repository=repo,
        local_similarity_service=local,
    )


# ---------- Verdict ladder ----------


def test_verdict_identical_on_file_hash_match() -> None:
    service = _build_service(
        [_history_doc(file_hash="hash-A", text_hash="other", scenario_id="prev")]
    )
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="hash-A",
        current_text_hash="hash-B",
        current_cleaned_text="anything",
    )
    assert verdict.verdict == "identical"
    assert verdict.score == 1.0
    assert verdict.match_type == "file_hash"
    assert verdict.is_renewal_candidate is True
    assert verdict.matched_analysis is not None
    assert verdict.matched_analysis.scenario_id == "prev"
    assert "empreinte SHA-256" in verdict.reason.lower() or "binaire" in verdict.reason.lower()


def test_verdict_identical_on_text_hash_match() -> None:
    service = _build_service(
        [_history_doc(file_hash="other", text_hash="hash-T", scenario_id="prev")]
    )
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="different",
        current_text_hash="hash-T",
        current_cleaned_text="anything",
    )
    assert verdict.verdict == "identical"
    assert verdict.match_type == "text_hash"
    assert verdict.is_renewal_candidate is True


def test_verdict_near_identical_when_jaccard_above_95pct() -> None:
    # Build two highly overlapping texts. Both texts share 99 out of 100
    # word-shingles → Jaccard ≈ 0.99.
    base = " ".join(f"mot{i}" for i in range(100))
    variant = base + " variation"
    service = _build_service(
        [_history_doc(file_hash="x", text_hash="y", cleaned_text=base)]
    )
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="new-file-hash",
        current_text_hash="new-text-hash",
        current_cleaned_text=variant,
    )
    assert verdict.score >= NEAR_IDENTICAL_THRESHOLD
    assert verdict.verdict == "near_identical"
    assert verdict.is_renewal_candidate is True


def test_verdict_highly_similar_in_80_94_band() -> None:
    # Word-shingles use size=5 → calibrate so that the jaccard lands in
    # [0.80, 0.95). With 90 shared words + 10 distinct on each side, the
    # ratio is 86 / (86 + 10 + 10) ≈ 0.811 — right inside the band.
    common = " ".join(f"mot{i}" for i in range(90))
    history_text = common + " " + " ".join(f"old{i}" for i in range(10))
    current_text = common + " " + " ".join(f"new{i}" for i in range(10))
    service = _build_service(
        [_history_doc(file_hash="x", text_hash="y", cleaned_text=history_text)]
    )
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="new",
        current_text_hash="new",
        current_cleaned_text=current_text,
    )
    assert HIGHLY_SIMILAR_THRESHOLD <= verdict.score < NEAR_IDENTICAL_THRESHOLD
    assert verdict.verdict == "highly_similar"
    assert verdict.is_renewal_candidate is False


def test_verdict_different_when_no_overlap() -> None:
    service = _build_service(
        [_history_doc(file_hash="x", text_hash="y", cleaned_text="alpha beta gamma delta")]
    )
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="new",
        current_text_hash="new",
        current_cleaned_text="totally different words here please now",
    )
    assert verdict.verdict == "different"
    assert verdict.is_renewal_candidate is False
    assert verdict.matched_analysis is None


def test_verdict_different_when_history_empty() -> None:
    service = _build_service([])
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="x",
        current_text_hash="y",
        current_cleaned_text="hello world",
    )
    assert verdict.verdict == "different"
    assert verdict.candidates_compared == 0
    assert verdict.matched_analysis is None


def test_verdict_excludes_current_scenario_from_history() -> None:
    """A document with the same scenario_id as the current upload (e.g. a
    re-analysis triggered manually) must NOT be flagged as a self-duplicate."""
    service = _build_service(
        [_history_doc(scenario_id="self", file_hash="x", cleaned_text="alpha")]
    )
    verdict = service.compute(
        current_scenario_id="self",
        current_file_hash="x",
        current_text_hash="y",
        current_cleaned_text="alpha",
    )
    assert verdict.verdict == "different"
    assert verdict.matched_analysis is None


def test_verdict_includes_extras_when_multiple_close_matches() -> None:
    base = " ".join(f"w{i}" for i in range(60))
    docs = [
        _history_doc(scenario_id=f"S{i}", file_hash=f"h{i}", cleaned_text=base)
        for i in range(4)
    ]
    service = _build_service(docs)
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="new",
        current_text_hash="new",
        current_cleaned_text=base,
    )
    # All 4 history docs are near-identical → primary match + up to 3 extras.
    assert verdict.verdict in {"identical", "near_identical"}
    assert verdict.matched_analysis is not None
    assert len(verdict.extras) <= 3


# ---------- Serialization ----------


def test_to_dict_shape_is_json_safe() -> None:
    service = _build_service(
        [_history_doc(file_hash="x", text_hash="y", cleaned_text="alpha")]
    )
    verdict = service.compute(
        current_scenario_id="new",
        current_file_hash="x",
        current_text_hash="z",
        current_cleaned_text="alpha",
    )
    payload = verdict.to_dict()
    # Mandatory keys for the frontend banner.
    for key in (
        "verdict",
        "score",
        "score_percent",
        "match_type",
        "is_renewal_candidate",
        "reason",
        "candidates_compared",
        "matched_analysis",
        "extras",
    ):
        assert key in payload, f"missing key: {key}"


# ---------- Integration via AnalysisService ----------


def _build_plagiarism_pipeline_with_strict(strict_stub):
    from unittest.mock import Mock

    from backend.pipelines.plagiarism_pipeline import PlagiarismPipeline

    return PlagiarismPipeline(
        local_similarity_service=Mock(),
        plagiarism_service=Mock(),
        strict_similarity_service=strict_stub,
        embedding_service=Mock(),
        vector_service=Mock(),
    )


def test_plagiarism_pipeline_falls_back_when_strict_verdict_raises() -> None:
    """If MongoDB blows up, the upload still succeeds with a placeholder."""
    broken = MagicMock()
    broken.compute.side_effect = RuntimeError("mongo down")

    pipeline = _build_plagiarism_pipeline_with_strict(broken)
    warnings: list[str] = []
    result = pipeline._compute_strict_match(  # noqa: SLF001
        scenario_id="new",
        file_hash="x",
        text_hash="y",
        cleaned_text="hi",
        warnings=warnings,
    )
    assert result["verdict"] == "different"
    assert result["is_renewal_candidate"] is False
    assert result["status"] == "unavailable"
    assert any("indisponible" in w.lower() for w in warnings)


def test_plagiarism_pipeline_returns_verdict_dict() -> None:
    from backend.services.strict_similarity_service import (
        StrictMatchedAnalysis,
        StrictVerdict,
    )

    verdict = StrictVerdict(
        verdict="identical",
        score=1.0,
        match_type="file_hash",
        is_renewal_candidate=True,
        reason="hash match",
        matched_analysis=StrictMatchedAnalysis(
            scenario_id="prev",
            original_filename="old.pdf",
            stored_filename="old.pdf",
            analyzed_at="2026-05-15T10:00:00+00:00",
            risk_level="low",
            file_hash="x",
            text_hash="y",
            similarity_score=1.0,
            match_type="file_hash",
        ),
        candidates_compared=1,
        extras=[],
    )
    stub = MagicMock()
    stub.compute.return_value = verdict
    pipeline = _build_plagiarism_pipeline_with_strict(stub)
    warnings: list[str] = []
    result = pipeline._compute_strict_match(  # noqa: SLF001
        scenario_id="new",
        file_hash="x",
        text_hash="y",
        cleaned_text="hi",
        warnings=warnings,
    )
    assert result["verdict"] == "identical"
    assert result["is_renewal_candidate"] is True
    assert result["matched_analysis"]["scenario_id"] == "prev"
    assert warnings == []

"""Tests for the advanced RAG layer (additive, on-demand)."""

from typing import Any

import pytest

from backend.services.advanced_rag_service import AdvancedRAGService
from backend.services.llm_provider import LLMResponse, MockLLMProvider


def _analysis_fixture(
    *,
    risk: str = "medium",
    matches: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    score: float = 0.45,
    total_matches: int = 3,
    total_sources: int = 1,
) -> dict[str, Any]:
    return {
        "scenario_id": "scenario-X",
        "document_stats": {
            "original_filename": "demo.pdf",
            "file_name": "abcd.pdf",
            "words_count": 1500,
            "chunks_count": 10,
        },
        "rag_report": {"risk_level": risk, "summary": "fixture"},
        "plagiarism": {
            "global_similarity_score": score,
            "total_matches": total_matches,
            "total_sources": total_sources,
            "matches": matches or [],
            "plagiarism_sources": sources or [],
        },
        "profanity": {
            "profanity_score": 0.0,
            "detected_words": [],
        },
        "adult_content": {"adult_content_score": 0.0, "risk_level": "low"},
    }


def _match(
    *,
    score: float = 0.55,
    text: str = "passage source extrait",
    current_text: str = "passage analysé",
    scenario: str = "scenario-S",
    filename: str = "source.pdf",
    overlap: str | None = "passage clé",
    current_pos: int | str | None = 1,
    source_pos: int | str | None = 0,
) -> dict[str, Any]:
    return {
        "similarity_score": score,
        "matched_chunk_text_display": text,
        "matched_chunk_text": text,
        "chunk_text": current_text,
        "overlap_text": overlap,
        "matched_scenario_id": scenario,
        "stored_filename": filename,
        "filename": filename,
        "original_filename": filename,
        "current_chunk_index": current_pos,
        "source_chunk_index": source_pos,
    }


# ---------- Service behaviour ----------


def test_generate_returns_structured_payload_with_mock_provider() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        matches=[
            _match(score=0.82, text="extrait source important"),
            _match(score=0.6, scenario="scenario-T"),
        ]
    )

    report = service.generate(analysis=analysis, scenario_id="scenario-X")

    assert set(report.keys()) >= {
        "scenario_id",
        "narrative",
        "context",
        "prompt",
        "llm",
        "generated_at",
    }
    assert report["scenario_id"] == "scenario-X"
    assert report["llm"]["provider"] == "mock"
    assert report["llm"]["used_fallback"] is True
    assert "Synthèse globale" in report["narrative"]
    assert "Analyse passage par passage" in report["narrative"]
    assert "Actions recommandées" in report["narrative"]
    assert "Conclusion" in report["narrative"]


def test_generate_prompt_contains_top_passages_metadata() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider(), max_passages=2)
    analysis = _analysis_fixture(
        matches=[
            _match(score=0.91, text="A", filename="alpha.pdf"),
            _match(score=0.42, text="B", filename="beta.pdf"),
            _match(score=0.30, text="C", filename="gamma.pdf"),
        ]
    )

    report = service.generate(analysis=analysis, scenario_id="scenario-X")

    assert "alpha.pdf" in report["prompt"]
    assert "beta.pdf" in report["prompt"]
    # max_passages = 2, gamma must be dropped
    assert "gamma.pdf" not in report["prompt"]


def test_generate_dedupes_passages_by_signature() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        matches=[
            _match(score=0.9, text="extrait identique", scenario="S1"),
            _match(score=0.8, text="extrait identique", scenario="S1"),
            _match(score=0.7, text="autre extrait", scenario="S2"),
        ]
    )

    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    passages = report["context"]["passages"]
    assert len(passages) == 2
    sources = {p["source_scenario_id"] for p in passages}
    assert sources == {"S1", "S2"}


def test_generate_handles_zero_matches() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        risk="low", matches=[], sources=[], score=0.0, total_matches=0,
        total_sources=0,
    )
    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    assert "Aucun passage similaire" in report["narrative"]
    assert report["context"]["passages"] == []
    assert report["context"]["risk_level"] == "low"
    assert (
        "Aucune atteinte evidente aux constantes nationales marocaines"
        in report["prompt"]
    )
    assert (
        "Aucune atteinte evidente aux constantes nationales marocaines"
        in report["narrative"]
    )


def test_moroccan_constants_flags_are_passed_to_rag_prompt() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(matches=[], score=0.0, total_matches=0)
    analysis["moroccan_constants"] = {
        "score": 0.6,
        "risk_level": "élevé",
        "flags": [
            {
                "category": "monarchy",
                "severity": "élevé",
                "chunk_index": 2,
                "evidence": "Le roi est un tyran corrompu.",
                "explanation": "Passage sensible touchant à la Monarchie constitutionnelle.",
            }
        ],
    }

    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    prompt = report["prompt"]
    narrative = report["narrative"]
    summary = report["context"]["moroccan_constants_summary"]

    assert summary["has_flags"] is True
    assert summary["flags"][0]["chunk_index"] == 2
    assert "PrincipesMarocPipeline" in prompt
    assert "Monarchie constitutionnelle" in prompt
    assert "Le roi est un tyran corrompu." in prompt
    assert "Le RAG doit seulement les expliquer" in prompt
    assert "Le roi est un tyran corrompu." in narrative
    assert "Constantes nationales marocaines" in narrative


def test_moroccan_constants_only_report_does_not_call_slow_llm() -> None:
    class FailingProvider:
        name = "slow"
        model = "slow-model"

        def complete(self, system: str, user: str) -> LLMResponse:
            raise AssertionError("LLM should not be called")

    service = AdvancedRAGService(llm_provider=FailingProvider())
    analysis = _analysis_fixture(matches=[], score=0.0, total_matches=0)
    analysis["moroccan_constants"] = {
        "score": 0.6,
        "risk_level": "élevé",
        "flags": [
            {
                "category": "monarchy",
                "severity": "élevé",
                "chunk_index": 2,
                "evidence": "Le roi est un tyran corrompu.",
                "explanation": "Passage sensible touchant à la Monarchie constitutionnelle.",
            }
        ],
    }

    report = service.generate(analysis=analysis, scenario_id="scenario-X")

    assert report["llm"]["provider"] == "mock"
    assert report["llm"]["used_fallback"] is True
    assert report["llm"]["error"] is None
    assert "Le roi est un tyran corrompu." in report["narrative"]


def test_exact_duplicate_prompt_prioritizes_internal_duplicate() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        risk="high",
        matches=[],
        sources=[],
        score=1.0,
        total_matches=0,
        total_sources=0,
    )
    analysis["plagiarism"].update(
        {
            "exact_duplicate": True,
            "duplicate_count": 3,
            "duplicate_analyses": [
                {
                    "scenario_id": "old-1",
                    "original_filename": "scenario.pdf",
                    "created_at": "2026-06-01T10:00:00+00:00",
                }
            ],
        }
    )

    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    prompt = report["prompt"]
    narrative = report["narrative"]

    assert report["context"]["exact_duplicate"] is True
    assert report["context"]["duplicate_count"] == 3
    assert "Mission prioritaire : DOUBLON EXACT" in prompt
    assert "doublon exact interne" in prompt.lower()
    assert "Aucun passage similaire significatif externe" in prompt
    assert "plagiat partial" not in prompt.lower()
    assert "similarite partiale" not in prompt.lower()
    assert "anciennes analyses identiques sont" in prompt
    assert "Aucun passage similaire significatif externe" in narrative
    assert "Le risque HIGH vient principalement du doublon exact interne" in narrative
    forbidden = [
        "reutilisation volontaire",
        "citation non autorisee",
        "plagiat confirme",
        "forte similitude avec plusieurs documents",
        "risque eleve de plagiat",
    ]
    for phrase in forbidden:
        assert phrase not in narrative.lower()


def test_exact_duplicate_filters_duplicate_matches_from_partial_passages() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        risk="high",
        matches=[
            {
                **_match(score=1.0, text="texte identique", filename="old.pdf"),
                "duplicate": True,
                "match_type": "exact_duplicate",
            }
        ],
        score=1.0,
        total_matches=1,
        total_sources=1,
    )
    analysis["plagiarism"].update(
        {
            "exact_duplicate": True,
            "duplicate_count": 1,
            "duplicate_analyses": [{"scenario_id": "old-1"}],
        }
    )

    report = service.generate(analysis=analysis, scenario_id="scenario-X")

    assert report["context"]["passages"] == []
    assert "Aucun passage similaire significatif externe" in report["narrative"]


def test_no_context_narrative_does_not_invent_passages() -> None:
    """With zero passages the report must NOT fabricate Passage 1 / Passage 2."""
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        risk="low",
        matches=[],
        sources=[],
        score=0.0,
        total_matches=0,
        total_sources=0,
    )

    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    narrative = report["narrative"]

    # Mandatory marker for no-context case.
    assert "Aucun passage similaire" in narrative
    # No fabricated passages.
    assert "Passage 1" not in narrative
    assert "Passage 2" not in narrative
    assert "Passage 3" not in narrative
    # The "Analyse passage par passage" section must NOT appear.
    assert "Analyse passage par passage" not in narrative
    # The dedicated no-context sections must appear instead.
    assert "Interprétation du score" in narrative
    assert "Limites de l'analyse" in narrative
    assert "Actions recommandées" in narrative
    assert "Conclusion" in narrative
    # The retrieved-passages payload remains empty.
    assert report["context"]["passages"] == []


def test_no_context_prompt_instructs_not_to_fabricate_passages() -> None:
    """The LLM prompt sent in the no-context case must forbid invention."""
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        matches=[], sources=[], total_matches=0, total_sources=0, score=0.0,
    )
    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    prompt = report["prompt"]
    # The prompt explicitly warns against fabricating passages.
    assert "Aucun passage similaire" in prompt
    assert "NE GÉNÈRE PAS" in prompt or "ne génère pas" in prompt.lower()
    assert "N'INVENTE PAS" in prompt or "n'invente pas" in prompt.lower()
    # The mandatory section list omits the passage-by-passage analysis.
    assert "Analyse passage par passage" not in prompt
    assert "Interprétation du score" in prompt
    assert "Limites de l'analyse" in prompt


def test_generate_validates_scenario_id() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    with pytest.raises(ValueError):
        service.generate(analysis={"plagiarism": {}}, scenario_id="   ")


def test_generate_uses_real_llm_when_provided() -> None:
    class StubProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, system: str, user: str) -> LLMResponse:
            # Surface that we actually received the prompt by echoing
            # a recognisable string.
            return LLMResponse(
                text="NARRATIVE FROM LLM",
                provider=self.name,
                model=self.model,
            )

    service = AdvancedRAGService(llm_provider=StubProvider())
    analysis = _analysis_fixture(matches=[_match()])

    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    assert report["narrative"] == "NARRATIVE FROM LLM"
    assert report["llm"]["provider"] == "stub"
    assert report["llm"]["used_fallback"] is False
    # The prompt sent to the LLM must contain the source filename.
    assert "source.pdf" in report["prompt"]


def test_generate_falls_back_when_llm_raises() -> None:
    class BrokenProvider:
        name = "broken"
        model = "broken-model"

        def complete(self, system: str, user: str) -> LLMResponse:
            raise RuntimeError("backend down")

    service = AdvancedRAGService(llm_provider=BrokenProvider())
    analysis = _analysis_fixture(matches=[_match()])

    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    assert report["llm"]["used_fallback"] is True
    assert report["llm"]["provider"] == "mock"
    assert "backend down" in (report["llm"]["error"] or "")
    assert "Synthèse globale" in report["narrative"]


def test_context_serialization_contains_position_metadata() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    analysis = _analysis_fixture(
        matches=[_match(current_pos=4, source_pos=7)]
    )
    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    passage = report["context"]["passages"][0]
    assert passage["current_position"] == "4"
    assert passage["source_position"] == "7"


def test_generate_groups_sources_into_passages() -> None:
    service = AdvancedRAGService(llm_provider=MockLLMProvider())
    sources = [
        {
            "source_scenario_id": "scenario-Z",
            "original_filename": "z.pdf",
            "stored_filename": "z.pdf",
            "best_score": 0.95,
            "matches_count": 2,
            "matches": [
                _match(score=0.95, text="depuis source Z #1"),
                _match(score=0.7, text="depuis source Z #2"),
            ],
        }
    ]
    analysis = _analysis_fixture(sources=sources, total_sources=1)
    report = service.generate(analysis=analysis, scenario_id="scenario-X")
    passages = report["context"]["passages"]
    assert any("depuis source Z #1" in p["source_excerpt"] for p in passages)
    assert all(p["source_filename"] == "z.pdf" for p in passages)

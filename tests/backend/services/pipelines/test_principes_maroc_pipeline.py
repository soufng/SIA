"""Tests for the Moroccan constants compliance pipeline.

Verifies the four risk buckets, the false-positive guard on neutral
mentions, and that the field is always present in the analysis result.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.pipelines.principes_maroc_pipeline import (
    LEVEL_ELEVE,
    LEVEL_FAIBLE,
    LEVEL_MOYEN,
    LEVEL_TRES_ELEVE,
    PrincipesMarocPipeline,
    escalate_risk_level,
    map_english_to_fr,
    map_fr_to_english,
)


@pytest.fixture
def pipeline() -> PrincipesMarocPipeline:
    return PrincipesMarocPipeline()


# ---------- Risk-level fixtures ----------


def _split(text: str, n: int = 1) -> list[str]:
    """Tiny helper to make ``chunks`` non-empty without re-running chunking."""
    if n <= 1:
        return [text]
    step = max(1, len(text) // n)
    return [text[i : i + step] for i in range(0, len(text), step)]


# ---------- 1. Neutral mentions ----------


def test_neutral_mentions_stay_low_risk(pipeline: PrincipesMarocPipeline) -> None:
    """Generic institutional vocabulary (Maroc, Islam, Sahara, Constitution)
    without any royal-family persona must not raise a flag.

    Royal personas are auto-flagged regardless of context (see the dedicated
    persona test below), so they are intentionally absent here.
    """
    text = (
        "Le Maroc est un royaume du Maghreb. "
        "La majorité des Marocains pratiquent l'islam. "
        "La constitution garantit la diversité et la démocratie. "
        "Le Sahara est une région importante du sud du pays."
    )
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_FAIBLE
    assert result["score"] < 0.25
    assert result["flags"] == []


def test_royal_persona_mention_is_auto_flagged(
    pipeline: PrincipesMarocPipeline,
) -> None:
    """Any nominative mention of a royal-family member (past or present)
    must surface for manual review — even when the surrounding text is
    descriptive or laudatory."""
    cases = [
        "Mohammed 6 ouvre le Festival de Marrakech.",
        "Le roi Mohammed VI a présenté sa nouvelle réforme.",
        "Feu le roi Hassan II évoquait souvent ce sujet.",
        "Le prince héritier Moulay El Hassan étudie à Rabat.",
        "Lalla Salma a fondé une association.",
    ]
    for text in cases:
        result = pipeline.analyze(text=text, chunks=_split(text))
        assert result["risk_level"] != LEVEL_FAIBLE, (
            f"Expected a flag for royal persona mention: {text!r}"
        )
        assert any(
            flag["category"] == "monarchy" for flag in result["flags"]
        ), f"Expected monarchy flag for: {text!r}"


def test_history_lesson_is_low_risk(pipeline: PrincipesMarocPipeline) -> None:
    text = (
        "Au cours du XXe siècle, la monarchie marocaine a connu plusieurs "
        "réformes constitutionnelles. L'islam y est la religion d'État."
    )
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_FAIBLE


# ---------- 2. Ambiguous / ironic ----------


def test_ambiguous_irony_is_medium_risk(pipeline: PrincipesMarocPipeline) -> None:
    """Critical-but-not-violent rhetoric should land in 'moyen'."""
    text = (
        "Cette soi-disant démocratie ressemble à une dictature douce. "
        "L'oppression silencieuse pèse sur le pays."
    )
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_MOYEN
    assert any(flag["severity"] == LEVEL_MOYEN for flag in result["flags"])


# ---------- 3. Explicit attack on an institution ----------


def test_explicit_attack_is_high_risk(pipeline: PrincipesMarocPipeline) -> None:
    text = "Le roi est un dictateur corrompu, un tyran illégitime."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_ELEVE
    assert any(flag["severity"] == LEVEL_ELEVE for flag in result["flags"])
    # Monarchy category specifically should be tagged.
    monarchy_flags = [
        f for f in result["flags"] if f["category"] == "monarchy"
    ]
    assert len(monarchy_flags) >= 1


def test_explicit_attack_on_islam_is_high_risk(
    pipeline: PrincipesMarocPipeline,
) -> None:
    text = "L'islam est une religion arriérée et barbare, à rejeter."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] in {LEVEL_ELEVE, LEVEL_TRES_ELEVE}
    assert any(f["category"] == "islam" for f in result["flags"])


# ---------- 4. Very high risk — violence / hate / separatism ----------


def test_call_to_violence_is_very_high(pipeline: PrincipesMarocPipeline) -> None:
    text = "Il faut tuer le roi et détruire la monarchie."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_TRES_ELEVE
    assert result["score"] >= 0.75


def test_separatism_call_is_very_high(pipeline: PrincipesMarocPipeline) -> None:
    text = "Nous appelons à l'indépendance du Sahara et au Polisario."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_TRES_ELEVE
    assert any(f["category"] == "national_unity" for f in result["flags"])


def test_hate_speech_is_very_high(pipeline: PrincipesMarocPipeline) -> None:
    text = "Mort aux musulmans, tous des terroristes."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_TRES_ELEVE


# ---------- 5. False-positive guard ----------


def test_isolated_neutral_keywords_do_not_trigger(
    pipeline: PrincipesMarocPipeline,
) -> None:
    """A single neutral word per sentence must NOT escalate the risk."""
    text = (
        "Sahara. Constitution. Roi. Islam. Démocratie. Maroc. Monarchie."
    )
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_FAIBLE
    assert result["flags"] == []


def test_critical_word_far_from_subject_does_not_trigger(
    pipeline: PrincipesMarocPipeline,
) -> None:
    """A trigger word must be near a protected subject. If they are far
    apart, no flag is raised."""
    text = (
        "Le Maroc accueille de nombreux touristes chaque année. "
        + " " * 200
        + "Dans un autre contexte, on parle parfois d'injustice."
    )
    result = pipeline.analyze(text=text, chunks=[text])
    # Far enough apart -> no flag for "national_unity".
    assert all(f["category"] != "national_unity" for f in result["flags"])


# ---------- 5b. Neutral mentions are surfaced separately ----------


def test_neutral_mentions_are_collected_without_raising_risk(
    pipeline: PrincipesMarocPipeline,
) -> None:
    """Every occurrence of a protected subject lands in ``mentions``.

    A purely narrative scenario about ``le roi`` must produce zero flags
    (no severity trigger nearby) but many mentions so the operator can
    scan the full coverage manually.
    """
    text = (
        "Le roi entra dans le palais. Le roi salua la cour. "
        "Le tailleur du roi attendait, silencieux. "
        "Plus tard, le roi monta sur son trône."
    )
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["risk_level"] == LEVEL_FAIBLE
    assert result["flags"] == []
    # The narrative mentions "roi" 4 times — dedup may collapse identical
    # snippets but we expect at least one mention surfaced.
    assert result["mentions_total"] >= 1
    monarchy_mentions = [
        m for m in result["mentions"] if m["category"] == "monarchy"
    ]
    assert len(monarchy_mentions) >= 1
    # Neutral mentions have ``flagged_severity = None``.
    for mention in monarchy_mentions:
        assert mention.get("flagged_severity") is None


def test_mentions_include_flagged_severity_when_trigger_present(
    pipeline: PrincipesMarocPipeline,
) -> None:
    """A risky mention is in BOTH lists with ``flagged_severity`` set."""
    text = "Ce roi est un tyran corrompu."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert any(
        m.get("flagged_severity") is not None for m in result["mentions"]
    )


def test_mentions_by_category_groups_counts(
    pipeline: PrincipesMarocPipeline,
) -> None:
    text = (
        "Le roi du Maroc est respecté. La constitution garantit la "
        "démocratie. L'islam est la religion d'État."
    )
    result = pipeline.analyze(text=text, chunks=_split(text))
    counts = result["mentions_by_category"]
    assert set(counts.keys()) == {
        "islam",
        "national_unity",
        "monarchy",
        "democratic_choice",
    }
    assert counts["monarchy"] >= 1
    assert counts["national_unity"] >= 1
    assert counts["democratic_choice"] >= 1
    assert counts["islam"] >= 1


# ---------- 6. Field always present + shape ----------


def test_moroccan_constants_field_is_always_present(
    pipeline: PrincipesMarocPipeline,
) -> None:
    """Even when nothing is detected, the result must keep the documented
    shape so downstream code never has to do ``KeyError`` handling."""
    result = pipeline.analyze(text="Une histoire d'amour simple.", chunks=[])
    assert set(result.keys()) >= {
        "score",
        "risk_level",
        "flags",
        "categories",
    }
    assert result["score"] == 0.0
    assert result["risk_level"] == LEVEL_FAIBLE
    assert result["flags"] == []
    assert set(result["categories"].keys()) == {
        "islam",
        "national_unity",
        "monarchy",
        "democratic_choice",
    }
    for cat_info in result["categories"].values():
        assert {"count", "risk_level", "score"} <= set(cat_info.keys())


def test_each_flag_has_documented_shape(
    pipeline: PrincipesMarocPipeline,
) -> None:
    text = "Le roi est un tyran corrompu."
    result = pipeline.analyze(text=text, chunks=_split(text))
    assert result["flags"], "expected at least one flag"
    for flag in result["flags"]:
        assert set(flag.keys()) >= {
            "category",
            "severity",
            "chunk_index",
            "evidence",
            "explanation",
        }
        # The explanation always carries the manual-review wording.
        assert "examiner manuellement" in flag["explanation"]
        assert flag["category"] in {
            "islam",
            "national_unity",
            "monarchy",
            "democratic_choice",
        }
        assert flag["severity"] in {
            LEVEL_FAIBLE,
            LEVEL_MOYEN,
            LEVEL_ELEVE,
            LEVEL_TRES_ELEVE,
        }


# ---------- Validation ----------


def test_analyze_raises_for_non_string_text(
    pipeline: PrincipesMarocPipeline,
) -> None:
    with pytest.raises(TypeError):
        pipeline.analyze(text=None, chunks=[])  # type: ignore[arg-type]


def test_analyze_raises_for_non_list_chunks(
    pipeline: PrincipesMarocPipeline,
) -> None:
    with pytest.raises(TypeError):
        pipeline.analyze(text="hello", chunks="hello")  # type: ignore[arg-type]


# ---------- Mapping helpers ----------


def test_map_fr_to_english_translates_levels() -> None:
    assert map_fr_to_english(LEVEL_FAIBLE) == "low"
    assert map_fr_to_english(LEVEL_MOYEN) == "medium"
    assert map_fr_to_english(LEVEL_ELEVE) == "high"
    assert map_fr_to_english(LEVEL_TRES_ELEVE) == "tres_eleve"


def test_map_english_to_fr_translates_levels() -> None:
    assert map_english_to_fr("low") == LEVEL_FAIBLE
    assert map_english_to_fr("medium") == LEVEL_MOYEN
    assert map_english_to_fr("high") == LEVEL_ELEVE
    assert map_english_to_fr("tres_eleve") == LEVEL_TRES_ELEVE


def test_escalate_risk_level_keeps_higher() -> None:
    assert escalate_risk_level("low", LEVEL_MOYEN) == "medium"
    assert escalate_risk_level("medium", LEVEL_FAIBLE) == "medium"
    assert escalate_risk_level("high", LEVEL_TRES_ELEVE) == "tres_eleve"
    assert escalate_risk_level("high", LEVEL_MOYEN) == "high"


# ---------- End-to-end integration with AnalysisService ----------


def test_analysis_service_result_contains_moroccan_constants_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The /uploads/analyze response must always include the new field."""
    from unittest.mock import Mock

    from backend.pipelines.document_pipeline import DocumentContext
    from backend.services.analysis_service import AnalysisService

    # A document context with neutral text — moroccan_constants should
    # come back as faible / 0 flags but the field MUST exist.
    doc = DocumentContext(
        scenario_id="s1",
        file_path="dummy.pdf",
        original_filename="dummy.pdf",
        file_hash="hash",
        text_hash="thash",
        raw_text="raw",
        cleaned_text="Un scenario familial sans contenu sensible.",
        display_text="display",
        repeated_lines=set(),
        chunks=["Un scenario familial sans contenu sensible."],
        display_chunks=["Un scenario familial sans contenu sensible."],
        chunk_metadata=[],
        document_stats={"chunks_count": 1},
        page_records=[],
    )

    document_pipeline = Mock()
    document_pipeline.run.return_value = doc
    plagiarism_pipeline = Mock()
    plagiarism_outcome = Mock()
    plagiarism_outcome.plagiarism_result = {
        "global_similarity_score": 0.0,
        "matches": [],
    }
    plagiarism_outcome.strict_match = {"verdict": "different"}
    plagiarism_outcome.vector_available = False
    plagiarism_pipeline.run.return_value = plagiarism_outcome
    moderation_pipeline = Mock()
    moderation_outcome = Mock()
    moderation_outcome.profanity_result = {"profanity_score": 0.0}
    moderation_outcome.adult_content_result = {"adult_content_score": 0.0}
    moderation_pipeline.run.return_value = moderation_outcome
    template = Mock()
    template.generate_report.return_value = {"risk_level": "low"}

    service = AnalysisService(
        document_pipeline=document_pipeline,
        plagiarism_pipeline=plagiarism_pipeline,
        moderation_pipeline=moderation_pipeline,
        template_report_service=template,
        # We don't need the real ones for this test.
        pdf_service=Mock(),
        text_cleaning_service=Mock(),
        chunking_service=Mock(),
        local_similarity_service=Mock(),
        profanity_service=Mock(),
        adult_content_service=Mock(),
        strict_similarity_service=Mock(),
    )

    result = service.analyze_scenario(
        scenario_id="s1",
        file_path="dummy.pdf",
        original_filename="dummy.pdf",
    )
    assert "moroccan_constants" in result
    mc = result["moroccan_constants"]
    assert mc["risk_level"] == LEVEL_FAIBLE
    assert mc["flags"] == []
    assert {"score", "risk_level", "flags", "categories", "mentions"} <= set(
        mc.keys()
    )

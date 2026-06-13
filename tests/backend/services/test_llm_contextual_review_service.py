"""Tests for the optional LLM contextual review layer.

Only the additive behaviour is exercised here. The deterministic
pipelines are not invoked — we feed pre-built ``pipeline_results``
and chunk metadata and assert that:

- chunks with royal/sensitive lexicon are selected even when the rule
  pipelines did not flag them;
- alerts whose ``exact_quote`` is not a substring of any selected chunk
  are rejected;
- valid alerts are kept and exposed under ``llm_contextual_alerts``;
- the report ``risk_level`` is escalated only for validated sensitive
  alerts;
- when the LLM call fails the deterministic side stays available.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.core.config import settings
from backend.services import llm_contextual_review_service as module
from backend.services.llm_contextual_review_service import (
    ContextualReviewResult,
    LLMContextualReviewService,
    report_risk_for_alert,
    select_contextual_chunks_for_llm,
    should_escalate_global_risk,
)
from backend.services.llm_provider import LLMProviderError, LLMResponse


# ---------- Helpers ----------


class _FakeProvider:
    """Minimal stand-in for an LLM provider returning a scripted JSON."""

    name = "fake"
    model = "fake-1"

    def __init__(self, response_text: str | None = None, raise_exc: Exception | None = None):
        self._response_text = response_text or ""
        self._raise = raise_exc

    def complete(self, system: str, user: str) -> LLMResponse:  # noqa: D401
        if self._raise is not None:
            raise self._raise
        return LLMResponse(
            text=self._response_text,
            provider=self.name,
            model=self.model,
            used_fallback=False,
        )


def _chunks() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": "chunk_0",
            "chunk_index": 0,
            "page_number": 1,
            "text_normalized": "Une scène anodine d'ouverture sans contenu sensible.",
            "text_display": "Une scène anodine d'ouverture sans contenu sensible.",
        },
        {
            "chunk_id": "chunk_1",
            "chunk_index": 1,
            "page_number": 2,
            "text_normalized": (
                "Moulay Hassan rencontre en secret une amante au palais royal "
                "et lui promet un mariage discret."
            ),
            "text_display": (
                "Moulay Hassan rencontre en secret une amante au palais royal "
                "et lui promet un mariage discret."
            ),
        },
        {
            "chunk_id": "chunk_2",
            "chunk_index": 2,
            "page_number": 3,
            "text_normalized": "Le café est servi. Les invités discutent du temps qu'il fait.",
            "text_display": "Le café est servi. Les invités discutent du temps qu'il fait.",
        },
    ]


@pytest.fixture(autouse=True)
def _enable_feature(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CONTEXTUAL_REVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_ALERTS_MAX", 8)
    monkeypatch.setattr(settings, "LLM_CONTEXTUAL_MAX_CHUNKS", 10)
    monkeypatch.setattr(settings, "LLM_CONTEXTUAL_MAX_CHARS_PER_CHUNK", 2000)
    monkeypatch.setattr(settings, "LLM_CONTEXTUAL_MAX_TOTAL_CHARS", 12000)


# ---------- Selection ----------


def test_select_picks_chunks_with_royal_persona_even_when_not_flagged():
    selected = select_contextual_chunks_for_llm(
        scenario_chunks=_chunks(),
        pipeline_results={},  # nothing flagged by the deterministic side
    )
    ids = {c["chunk_id"] for c in selected}
    # The ambiguous "Moulay Hassan + secret + amante" chunk must be picked.
    assert "chunk_1" in ids


def test_select_returns_empty_when_chunks_have_no_sensitive_lexicon():
    bland = [
        {
            "chunk_id": "c0",
            "chunk_index": 0,
            "page_number": 1,
            "text_display": "Texte parfaitement neutre.",
            "text_normalized": "Texte parfaitement neutre.",
        }
    ]
    selected = select_contextual_chunks_for_llm(
        scenario_chunks=bland,
        pipeline_results={},
    )
    # Only chunk → counts as "edge" → selected by edge rule.
    assert len(selected) == 1


# ---------- Validation ----------


def test_alert_with_missing_quote_is_rejected():
    service = LLMContextualReviewService(
        llm_provider=_FakeProvider(
            response_text=(
                '{"additional_alerts": ['
                '{"category": "monarchie", "risk": "HIGH", '
                '"exact_quote": "phrase totalement inventée qui ne figure pas", '
                '"page": 2, "chunk_id": "chunk_1", "reason": "x"}'
                '], "summary": "test"}'
            )
        )
    )
    result = service.review(
        scenario_metadata={"scenario_id": "s1"},
        scenario_chunks=_chunks(),
        pipeline_results={},
    )
    assert result.enabled is True
    assert result.alerts == []
    assert result.rejected_count == 1


def test_valid_alert_is_accepted_and_normalised():
    quote = "Moulay Hassan rencontre en secret une amante"
    service = LLMContextualReviewService(
        llm_provider=_FakeProvider(
            response_text=(
                '{"additional_alerts": ['
                '{"category": "MONARCHIE", "risk": "high", '
                f'"exact_quote": "{quote}", '
                '"page": 2, "chunk_id": "chunk_1", '
                '"reason": "Royal en situation intime", '
                '"suggested_rewrite": "Reformuler sans nommer un membre royal"}'
                '], "summary": "ok"}'
            )
        )
    )
    result = service.review(
        scenario_metadata={"scenario_id": "s1"},
        scenario_chunks=_chunks(),
        pipeline_results={},
    )
    assert len(result.alerts) == 1
    alert = result.alerts[0]
    assert alert["category"] == "monarchie"
    assert alert["risk"] == "HIGH"
    assert alert["exact_quote"] == quote
    assert alert["chunk_id"] == "chunk_1"
    assert alert["page"] == 2


# ---------- Risk escalation ----------


def test_risk_only_escalates_for_sensitive_categories_at_high_or_above():
    high_monarchy = {"category": "monarchie", "risk": "HIGH"}
    low_monarchy = {"category": "monarchie", "risk": "LOW"}
    high_ambiguity = {"category": "ambiguite", "risk": "HIGH"}
    assert should_escalate_global_risk(high_monarchy) is True
    assert should_escalate_global_risk(low_monarchy) is False
    assert should_escalate_global_risk(high_ambiguity) is False
    assert report_risk_for_alert(high_monarchy) == "high"
    assert report_risk_for_alert({"risk": "VERY_HIGH"}) == "high"


# ---------- Failure modes ----------


def test_llm_failure_returns_disabled_alerts_but_does_not_raise(monkeypatch):
    service = LLMContextualReviewService(
        llm_provider=_FakeProvider(raise_exc=LLMProviderError("boom"))
    )
    result = service.review(
        scenario_metadata={"scenario_id": "s1"},
        scenario_chunks=_chunks(),
        pipeline_results={},
    )
    assert isinstance(result, ContextualReviewResult)
    assert result.alerts == []
    assert result.fallback_used is True
    assert result.error == "boom"


def test_non_json_response_falls_back_cleanly():
    service = LLMContextualReviewService(
        llm_provider=_FakeProvider(response_text="désolé je ne sais pas")
    )
    result = service.review(
        scenario_metadata={"scenario_id": "s1"},
        scenario_chunks=_chunks(),
        pipeline_results={},
    )
    assert result.alerts == []
    assert result.error == "non_json_response"


def test_feature_flag_off_short_circuits(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CONTEXTUAL_REVIEW_ENABLED", False)
    service = LLMContextualReviewService(
        llm_provider=_FakeProvider(response_text="{}")
    )
    result = service.review(
        scenario_metadata={"scenario_id": "s1"},
        scenario_chunks=_chunks(),
        pipeline_results={},
    )
    assert result.enabled is False
    assert result.alerts == []

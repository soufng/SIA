"""Tests for the LLM-based reranker."""

from __future__ import annotations

import pytest

from backend.services.llm_provider import LLMResponse
from backend.services.llm_reranker import LLMReranker, _parse_score_list


class _StubLLM:
    name = "stub"
    model = "stub"

    def __init__(self, text: str, raise_exc: Exception | None = None) -> None:
        self._text = text
        self._raise = raise_exc
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        if self._raise is not None:
            raise self._raise
        return LLMResponse(text=self._text, provider=self.name, model=self.model)


# ---------- _parse_score_list ----------


def test_parse_score_list_handles_plain_json() -> None:
    items, err = _parse_score_list('[{"i": 0, "s": 8}, {"i": 1, "s": 3}]')
    assert err is None
    assert [(i.index, i.score) for i in items] == [(0, 8.0), (1, 3.0)]


def test_parse_score_list_handles_string_scores() -> None:
    items, err = _parse_score_list('[{"i": 0, "s": "7"}]')
    assert err is None
    assert items[0].index == 0 and items[0].score == 7.0


def test_parse_score_list_handles_markdown_fences_and_preamble() -> None:
    raw = 'Voici :\n```json\n[{"i":2,"s":9},{"i":0,"s":1}]\n```'
    items, err = _parse_score_list(raw)
    assert err is None
    assert [(i.index, i.score) for i in items] == [(2, 9.0), (0, 1.0)]


def test_parse_score_list_rejects_non_json() -> None:
    items, err = _parse_score_list("aucune idée")
    assert items == []
    assert err == "json_parse_failed"


def test_parse_score_list_drops_malformed_entries() -> None:
    raw = '[{"i":0,"s":5}, {"oops":true}, {"i":"x","s":3}, {"i":2,"s":7}]'
    items, _err = _parse_score_list(raw)
    assert [(i.index, i.score) for i in items] == [(0, 5.0), (2, 7.0)]


# ---------- LLMReranker ----------


def test_rerank_orders_candidates_by_llm_score() -> None:
    # Candidate 1 gets 9, candidate 0 gets 4, candidate 2 gets 7
    llm = _StubLLM('[{"i":0,"s":4},{"i":1,"s":9},{"i":2,"s":7}]')
    reranker = LLMReranker(llm_provider=llm)

    result = reranker.rerank(
        document_summary="scénario test",
        candidates=["passage A", "passage B", "passage C"],
    )

    assert result.used_fallback is False
    assert result.ordered_indexes == [1, 2, 0]
    assert result.scores == {0: 4.0, 1: 9.0, 2: 7.0}


def test_rerank_keeps_unscored_candidates_at_tail() -> None:
    """LLM only scored 2 of 3 candidates — the third must survive."""

    llm = _StubLLM('[{"i":0,"s":2},{"i":2,"s":8}]')
    reranker = LLMReranker(llm_provider=llm)

    result = reranker.rerank(
        document_summary="doc",
        candidates=["a", "b", "c"],
    )

    assert result.used_fallback is False
    # Scored: 2 (s=8), 0 (s=2). Unscored: 1 appended at tail.
    assert result.ordered_indexes == [2, 0, 1]


def test_rerank_falls_back_when_llm_raises() -> None:
    llm = _StubLLM("", raise_exc=RuntimeError("ollama down"))
    reranker = LLMReranker(llm_provider=llm)

    result = reranker.rerank(
        document_summary="doc", candidates=["a", "b", "c"]
    )

    assert result.used_fallback is True
    assert result.ordered_indexes == [0, 1, 2]
    assert result.parse_error is not None
    assert "llm_error" in result.parse_error


def test_rerank_falls_back_on_garbage_json() -> None:
    llm = _StubLLM("this is not json")
    reranker = LLMReranker(llm_provider=llm)

    result = reranker.rerank(
        document_summary="doc", candidates=["a", "b"]
    )

    assert result.used_fallback is True
    assert result.ordered_indexes == [0, 1]
    assert result.parse_error == "json_parse_failed"


def test_rerank_ignores_out_of_range_indexes() -> None:
    """Small models sometimes invent extra rows. They must be dropped."""

    llm = _StubLLM('[{"i":0,"s":3},{"i":5,"s":10},{"i":1,"s":8}]')
    reranker = LLMReranker(llm_provider=llm)

    result = reranker.rerank(
        document_summary="doc", candidates=["a", "b"]
    )

    assert result.used_fallback is False
    assert result.ordered_indexes == [1, 0]
    assert 5 not in result.scores


def test_rerank_handles_empty_and_single_candidate() -> None:
    reranker = LLMReranker(llm_provider=_StubLLM("[]"))
    assert reranker.rerank("doc", []).ordered_indexes == []
    assert reranker.rerank("doc", ["x"]).ordered_indexes == [0]


def test_rerank_prompt_contains_all_candidate_indexes() -> None:
    llm = _StubLLM('[{"i":0,"s":1},{"i":1,"s":2},{"i":2,"s":3}]')
    reranker = LLMReranker(llm_provider=llm)

    reranker.rerank("résumé", ["alpha", "beta", "gamma"])

    _system, user = llm.calls[0]
    assert "[0]" in user
    assert "[1]" in user
    assert "[2]" in user
    assert "résumé" in user


def test_rerank_truncates_long_excerpts_in_prompt() -> None:
    llm = _StubLLM('[{"i":0,"s":5},{"i":1,"s":2}]')
    reranker = LLMReranker(llm_provider=llm, max_excerpt_chars=20)

    long_text = "x" * 500
    reranker.rerank("doc", [long_text, "court"])

    _system, user = llm.calls[0]
    # The 500-char passage must not appear in full.
    assert long_text not in user
    assert "…" in user

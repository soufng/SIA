"""Tests for the multi-query retriever (LLM-driven query rewriting)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.llm_provider import LLMResponse
from backend.services.multi_query_retriever import (
    MultiQueryRetriever,
    _parse_query_list,
)


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


class _StubEmbedding:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._raise = raise_exc
        self.calls: list[tuple[list[str], bool]] = []

    def generate_embeddings(
        self, texts: list[str], is_query: bool = False
    ) -> list[list[float]]:
        self.calls.append((list(texts), is_query))
        if self._raise is not None:
            raise self._raise
        return [[0.1, 0.2, 0.3] for _ in texts]


class _StubVector:
    def __init__(
        self,
        hits_per_call: list[list[dict[str, Any]]] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._hits = hits_per_call or []
        self._raise = raise_exc
        self.calls: list[int] = []

    def search_similar_chunks(
        self, embedding: list[float], limit: int = 5
    ) -> list[dict[str, Any]]:
        self.calls.append(limit)
        if self._raise is not None:
            raise self._raise
        if not self._hits:
            return []
        return self._hits.pop(0)


def _hit(
    *,
    point_id: str,
    scenario: str,
    text: str = "extrait source",
    score: float = 0.8,
) -> dict[str, Any]:
    return {
        "id": point_id,
        "score": score,
        "payload": {
            "scenario_id": scenario,
            "chunk_text": text,
            "chunk_text_display": text,
        },
    }


# ---------- _parse_query_list ----------


def test_parse_query_list_handles_plain_json_array() -> None:
    parsed, err = _parse_query_list('["a", "b", "c"]')
    assert parsed == ["a", "b", "c"]
    assert err is None


def test_parse_query_list_handles_markdown_fences() -> None:
    parsed, err = _parse_query_list('```json\n["x", "y"]\n```')
    assert parsed == ["x", "y"]
    assert err is None


def test_parse_query_list_extracts_array_from_preamble() -> None:
    raw = 'Voici les requêtes :\n["arc père-fils", "trahison politique"]\nVoilà.'
    parsed, err = _parse_query_list(raw)
    assert parsed == ["arc père-fils", "trahison politique"]
    assert err is None


def test_parse_query_list_rejects_empty_response() -> None:
    parsed, err = _parse_query_list("")
    assert parsed == []
    assert err == "empty_response"


def test_parse_query_list_rejects_non_json_garbage() -> None:
    parsed, err = _parse_query_list("ceci n'est pas du JSON du tout")
    assert parsed == []
    assert err == "json_parse_failed"


# ---------- MultiQueryRetriever ----------


def test_retrieve_happy_path_merges_and_dedupes_hits() -> None:
    llm = _StubLLM('["arc père-fils", "trahison politique"]')
    embed = _StubEmbedding()
    vector = _StubVector(
        hits_per_call=[
            [_hit(point_id="p1", scenario="other", score=0.9)],
            [
                _hit(point_id="p1", scenario="other", score=0.7),  # dup
                _hit(point_id="p2", scenario="other2", score=0.6),
            ],
        ]
    )
    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=vector,
        num_queries=2,
        per_query_limit=5,
    )

    result = retriever.retrieve(
        document_excerpts=["chunk un assez long pour servir d'exemple"],
        exclude_scenario_id="current",
    )

    assert result.used_fallback is False
    assert [q.text for q in result.queries] == [
        "arc père-fils",
        "trahison politique",
    ]
    assert [h["id"] for h in result.merged_hits] == ["p1", "p2"]
    assert result.merged_hits[0]["matched_via_query"] == "arc père-fils"
    assert result.merged_hits[1]["matched_via_query"] == "trahison politique"


def test_retrieve_excludes_self_scenario_hits() -> None:
    llm = _StubLLM('["requête une"]')
    embed = _StubEmbedding()
    vector = _StubVector(
        hits_per_call=[
            [
                _hit(point_id="self", scenario="current"),
                _hit(point_id="ok", scenario="other"),
            ]
        ]
    )
    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=vector,
        num_queries=1,
    )

    result = retriever.retrieve(
        document_excerpts=["extrait"],
        exclude_scenario_id="current",
    )

    assert [h["id"] for h in result.merged_hits] == ["ok"]


def test_retrieve_falls_back_when_llm_returns_garbage() -> None:
    llm = _StubLLM("not a json array")
    embed = _StubEmbedding()
    vector = _StubVector()
    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=vector,
    )

    result = retriever.retrieve(
        document_excerpts=["extrait"], exclude_scenario_id="current"
    )

    assert result.used_fallback is True
    assert result.merged_hits == []
    assert result.parse_error == "json_parse_failed"
    # No embedding call should happen when query generation fails.
    assert embed.calls == []


def test_retrieve_falls_back_when_llm_raises() -> None:
    llm = _StubLLM("", raise_exc=RuntimeError("boom"))
    embed = _StubEmbedding()
    vector = _StubVector()
    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=vector,
    )

    result = retriever.retrieve(
        document_excerpts=["extrait"], exclude_scenario_id="current"
    )

    assert result.used_fallback is True
    assert result.merged_hits == []
    assert result.parse_error is not None
    assert "llm_error" in result.parse_error


def test_retrieve_falls_back_when_embedding_fails() -> None:
    llm = _StubLLM('["q1", "q2"]')
    embed = _StubEmbedding(raise_exc=RuntimeError("embedder down"))
    vector = _StubVector()
    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=vector,
    )

    result = retriever.retrieve(
        document_excerpts=["extrait"], exclude_scenario_id="current"
    )

    assert result.used_fallback is True
    assert result.parse_error == "embedding_failed"
    # Queries are still surfaced for diagnostics.
    assert [q.text for q in result.queries] == ["q1", "q2"]


def test_retrieve_handles_partial_qdrant_failure() -> None:
    """If only one query fails Qdrant, others should still produce hits."""

    llm = _StubLLM('["q1", "q2"]')
    embed = _StubEmbedding()

    class _FlakeyVector:
        def __init__(self) -> None:
            self.call = 0

        def search_similar_chunks(
            self, embedding: list[float], limit: int = 5
        ) -> list[dict[str, Any]]:
            self.call += 1
            if self.call == 1:
                raise RuntimeError("qdrant hiccup")
            return [_hit(point_id="p1", scenario="other")]

    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=_FlakeyVector(),
    )

    result = retriever.retrieve(
        document_excerpts=["extrait"], exclude_scenario_id="current"
    )

    assert result.used_fallback is False
    assert [h["id"] for h in result.merged_hits] == ["p1"]
    # First query reports no hits, second one does.
    assert result.queries[0].hits == []
    assert [h["id"] for h in result.queries[1].hits] == ["p1"]


def test_retrieve_returns_empty_for_blank_excerpts() -> None:
    retriever = MultiQueryRetriever(
        llm_provider=_StubLLM("[]"),
        embedding_service=_StubEmbedding(),
        vector_service=_StubVector(),
    )
    result = retriever.retrieve(
        document_excerpts=["   ", ""], exclude_scenario_id="current"
    )
    assert result.used_fallback is True
    assert result.queries == []
    assert result.merged_hits == []


def test_retrieve_caps_to_num_queries_and_dedupes() -> None:
    """LLM over-generates and duplicates — retriever must clean up."""

    llm = _StubLLM('["a", "A", "b", "c", "d"]')
    embed = _StubEmbedding()
    vector = _StubVector(hits_per_call=[[], [], []])
    retriever = MultiQueryRetriever(
        llm_provider=llm,
        embedding_service=embed,
        vector_service=vector,
        num_queries=3,
    )

    result = retriever.retrieve(
        document_excerpts=["extrait"], exclude_scenario_id="current"
    )

    assert [q.text for q in result.queries] == ["a", "b", "c"]

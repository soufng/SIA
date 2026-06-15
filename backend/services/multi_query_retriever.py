"""Multi-query retriever: LLM-driven query rewriting for the advanced RAG.

The plagiarism pipeline only looks for chunks whose embedding is close to a
chunk of the uploaded document. That catches paraphrases and near-duplicates
but misses semantic plagiarism: cultural transposition, translation, scene
reordering, register shifts. This retriever asks an LLM to read a short
sample of the document and to produce N higher-level semantic queries
("father-son redemption arc in rural setting", "political betrayal involving
an authority figure"). Each query is embedded and searched against Qdrant,
results are merged and deduplicated.

The component is additive and toggled off by default
(``ADVANCED_RAG_MULTI_QUERY_ENABLED``). When the LLM fails to produce valid
JSON — which is common with small Ollama models — the caller falls back to
the existing longest-chunks strategy so behaviour never gets worse.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from backend.services.embedding_service import EmbeddingService
from backend.services.llm_provider import LLMProvider
from backend.services.vector_service import VectorService


logger = logging.getLogger(__name__)


_QUERY_GEN_SYSTEM_PROMPT = (
    "Tu es un assistant d'analyse de scénarios audiovisuels. Tu génères des "
    "requêtes de recherche sémantique courtes (5 à 12 mots) en français pour "
    "retrouver des scénarios similaires dans un corpus. Chaque requête doit "
    "capturer un arc narratif, un thème, un type de scène ou une situation "
    "dramatique — PAS des phrases littérales du document. Tu réponds "
    "UNIQUEMENT avec un tableau JSON de chaînes, sans commentaire."
)


_JSON_ARRAY_RE = re.compile(r"\[\s*\".*?\"\s*(?:,\s*\".*?\"\s*)*\]", re.DOTALL)


@dataclass
class GeneratedQuery:
    """One semantic query produced by the LLM plus its retrieval results."""

    text: str
    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MultiQueryResult:
    """Aggregate output of a multi-query retrieval pass."""

    queries: list[GeneratedQuery]
    merged_hits: list[dict[str, Any]]
    used_fallback: bool
    parse_error: str | None = None


class MultiQueryRetriever:
    """Generate semantic queries from a document then search Qdrant."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
        num_queries: int = 4,
        per_query_limit: int = 5,
    ) -> None:
        self.llm_provider = llm_provider
        self.embedding_service = embedding_service
        self.vector_service = vector_service
        self.num_queries = max(1, num_queries)
        self.per_query_limit = max(1, per_query_limit)

    def retrieve(
        self,
        document_excerpts: list[str],
        exclude_scenario_id: str,
    ) -> MultiQueryResult:
        """Run query generation + Qdrant search and return aggregated hits.

        Hits whose ``payload.scenario_id`` equals ``exclude_scenario_id``
        are dropped: a scenario must never match itself.
        """
        excerpts = [e.strip() for e in document_excerpts if e and e.strip()]
        if not excerpts:
            return MultiQueryResult(queries=[], merged_hits=[], used_fallback=True)

        queries, parse_error = self._generate_queries(excerpts)
        if not queries:
            return MultiQueryResult(
                queries=[],
                merged_hits=[],
                used_fallback=True,
                parse_error=parse_error,
            )

        try:
            embeddings = self.embedding_service.generate_embeddings(
                queries, is_query=True
            )
        except Exception:
            logger.exception("Multi-query retriever: embedding step failed.")
            return MultiQueryResult(
                queries=[GeneratedQuery(text=q) for q in queries],
                merged_hits=[],
                used_fallback=True,
                parse_error="embedding_failed",
            )

        generated: list[GeneratedQuery] = []
        seen_point_ids: set[str] = set()
        merged: list[dict[str, Any]] = []

        for query_text, embedding in zip(queries, embeddings):
            try:
                hits = self.vector_service.search_similar_chunks(
                    embedding=embedding, limit=self.per_query_limit
                )
            except Exception:
                logger.exception(
                    "Multi-query retriever: Qdrant search failed for %r.",
                    query_text,
                )
                generated.append(GeneratedQuery(text=query_text, hits=[]))
                continue

            kept: list[dict[str, Any]] = []
            for hit in hits:
                payload = hit.get("payload") or {}
                if str(payload.get("scenario_id") or "") == exclude_scenario_id:
                    continue
                point_id = str(hit.get("id") or "")
                if point_id and point_id in seen_point_ids:
                    continue
                if point_id:
                    seen_point_ids.add(point_id)
                # Tag with the query that surfaced it for diagnostics /
                # downstream "why was this passage retrieved" reporting.
                annotated = dict(hit)
                annotated["matched_via_query"] = query_text
                kept.append(annotated)
                merged.append(annotated)
            generated.append(GeneratedQuery(text=query_text, hits=kept))

        merged.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
        return MultiQueryResult(
            queries=generated,
            merged_hits=merged,
            used_fallback=False,
            parse_error=None,
        )

    def _generate_queries(
        self, excerpts: list[str]
    ) -> tuple[list[str], str | None]:
        """Ask the LLM for N semantic queries, return them plus a parse note."""
        sample = "\n\n---\n\n".join(excerpts[:3])
        user_prompt = (
            f"Voici un extrait représentatif d'un scénario à analyser :\n\n"
            f"{sample}\n\n"
            f"Génère exactement {self.num_queries} requêtes sémantiques "
            f"distinctes (5 à 12 mots chacune) pour retrouver des scénarios "
            f"similaires dans un corpus. Privilégie : arcs narratifs, "
            f"situations dramatiques, dynamiques de personnages, thèmes. "
            f"Réponds UNIQUEMENT par un tableau JSON de chaînes.\n\n"
            f'Exemple de format attendu : ["requête une", "requête deux"]'
        )

        try:
            response = self.llm_provider.complete(
                system=_QUERY_GEN_SYSTEM_PROMPT, user=user_prompt
            )
        except Exception as exc:
            logger.warning("Multi-query: LLM call failed: %s", exc)
            return [], f"llm_error: {exc}"

        queries, parse_error = _parse_query_list(response.text)
        if not queries:
            return [], parse_error

        # Deduplicate while preserving order and cap to num_queries.
        seen: set[str] = set()
        unique: list[str] = []
        for q in queries:
            key = q.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            unique.append(q.strip())
            if len(unique) >= self.num_queries:
                break
        return unique, None


def _parse_query_list(raw: str) -> tuple[list[str], str | None]:
    """Best-effort extraction of a JSON array of strings from an LLM reply.

    Small local models often wrap the JSON in markdown fences or add a
    preamble ("Voici les requêtes :"). We try, in order: direct JSON load,
    then a regex that captures the first ``["...", "..."]`` block.
    """
    text = (raw or "").strip()
    if not text:
        return [], "empty_response"

    # Strip markdown code fences if present.
    fenced = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        text = fenced.group(1).strip()

    candidates: list[str] = [text]
    match = _JSON_ARRAY_RE.search(text)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            strings = [
                str(item).strip()
                for item in parsed
                if isinstance(item, str) and item.strip()
            ]
            if strings:
                return strings, None

    return [], "json_parse_failed"

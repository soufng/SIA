"""Advanced RAG layer that produces an explanatory plagiarism narrative.

This service sits *on top* of the existing analysis pipeline. It never
mutates the analysis result it receives. Its responsibilities are:

1. **Retrieve**: pick the most informative passages from the plagiarism
   matches that already exist on the analysis result (these matches were
   computed earlier by ``PlagiarismService`` and ``LocalSimilarityService``).
2. **Augment**: build a structured prompt that contains those passages plus
   a compact analysis summary.
3. **Generate**: ask the configured LLM provider to write a natural-language
   explanation. When no real LLM is configured the service falls back to a
   deterministic enriched template so the endpoint always returns a polished
   answer.

The service exposes a single ``generate(...)`` method that returns a
``dict`` ready to be serialised as JSON.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.core.config import settings
from backend.services.embedding_service import EmbeddingService
from backend.services.llm_provider import (
    LLMProvider,
    MockLLMProvider,
    _redact,
    get_llm_provider,
)
from backend.services.llm_reranker import LLMReranker
from backend.services.multi_query_retriever import MultiQueryRetriever
from backend.services.vector_service import VectorService


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "Tu es un assistant éditorial spécialisé dans l'analyse de scénarios "
    "audiovisuels. Tu reçois des extraits d'un scénario uploadé et les "
    "passages similaires retrouvés dans un corpus existant. Ta mission est "
    "de produire un rapport explicatif clair en français, structuré, "
    "professionnel et factuel. Tu dois t'appuyer UNIQUEMENT sur les "
    "informations fournies et ne JAMAIS inventer de faits, de noms ou de "
    "scores. Quand l'information n'est pas disponible, dis-le. Le rapport "
    "doit aider un comité de lecture à décider si le scénario nécessite "
    "une révision avant validation."
)


# ---------- Data shapes ----------


@dataclass
class RetrievedPassage:
    """One overlap kept for the LLM context."""

    rank: int
    source_filename: str
    source_scenario_id: str
    score_pct: float
    current_position: str
    source_position: str
    current_excerpt: str
    source_excerpt: str
    overlap: str | None
    grouped_copies: int


@dataclass
class RAGContext:
    """Everything the LLM needs to write the narrative."""

    scenario_id: str
    risk_level: str
    similarity_score_pct: float
    total_matches: int
    total_sources: int
    passages: list[RetrievedPassage]
    exact_duplicate: bool = False
    duplicate_count: int = 0
    duplicate_analyses: list[dict[str, Any]] = field(default_factory=list)
    document_summary: dict[str, Any] = field(default_factory=dict)
    moderation_summary: dict[str, Any] = field(default_factory=dict)
    moroccan_constants_summary: dict[str, Any] = field(default_factory=dict)
    # retrieval_status explains why ``passages`` may be empty.
    # Codes: "ok", "no_match", "qdrant_unavailable", "corpus_empty",
    #        "below_threshold".
    retrieval_status: str = "ok"
    retrieval_reason: str = ""
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)


# ---------- Service ----------


class AdvancedRAGService:
    """Generate an explanatory plagiarism report from existing analysis data."""

    MAX_EXCERPT_CHARS = 600

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        max_passages: int | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
        multi_query_retriever: MultiQueryRetriever | None = None,
        reranker: LLMReranker | None = None,
    ) -> None:
        self.llm_provider = llm_provider or get_llm_provider()
        self.max_passages = max_passages or settings.ADVANCED_RAG_MAX_PASSAGES
        # Embedding + Qdrant are only instantiated when the fallback
        # retrieval actually fires, so unrelated tests don't pay the
        # SentenceTransformer load cost.
        self._embedding_service = embedding_service
        self._vector_service = vector_service
        self._multi_query_retriever = multi_query_retriever
        self._reranker = reranker

    @property
    def candidate_pool_size(self) -> int:
        """How many passages to keep before the (optional) rerank step."""
        if settings.ADVANCED_RAG_RERANK_ENABLED:
            return max(
                settings.ADVANCED_RAG_RERANK_POOL_SIZE, self.max_passages
            )
        return self.max_passages

    @property
    def reranker(self) -> LLMReranker:
        if self._reranker is None:
            self._reranker = LLMReranker(llm_provider=self.llm_provider)
        return self._reranker

    @property
    def embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def vector_service(self) -> VectorService:
        if self._vector_service is None:
            self._vector_service = VectorService()
        return self._vector_service

    @property
    def multi_query_retriever(self) -> MultiQueryRetriever:
        """Lazy-build the multi-query retriever sharing services with self."""
        if self._multi_query_retriever is None:
            self._multi_query_retriever = MultiQueryRetriever(
                llm_provider=self.llm_provider,
                embedding_service=self.embedding_service,
                vector_service=self.vector_service,
                num_queries=settings.ADVANCED_RAG_MULTI_QUERY_COUNT,
                per_query_limit=max(self.max_passages, 5),
            )
        return self._multi_query_retriever

    def generate(
        self,
        analysis: dict[str, Any],
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        """Build an explanatory advanced-RAG report for the given analysis.

        Args:
            analysis: The full saved analysis document (as returned by
                ``/uploads/analyze`` or stored in MongoDB).
            scenario_id: Optional override; if missing we read it from
                ``analysis``.

        Returns:
            A serialisable dict containing the narrative, the retrieved
            passages (for traceability), the prompt actually used, and
            metadata about the LLM provider/model.
        """
        if not isinstance(analysis, dict):
            raise TypeError("analysis must be a dictionary")

        scenario = (
            scenario_id
            or analysis.get("scenario_id")
            or (analysis.get("rag_report") or {}).get("scenario_id")
            or ""
        )
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError("scenario_id must not be empty")

        context = self._build_context(analysis, scenario_id=scenario)

        user_prompt = self._render_user_prompt(context)
        # Mock provider returns the rendered prompt directly so callers
        # always get a polished narrative even without a real LLM.
        if isinstance(self.llm_provider, MockLLMProvider):
            user_payload = self._render_fallback_narrative(context)
        else:
            user_payload = user_prompt

        if self._should_use_deterministic_report(context):
            narrative = self._render_fallback_narrative(context)
            return {
                "scenario_id": scenario,
                "generated_at": datetime.now(UTC).isoformat(),
                "narrative": narrative,
                "context": self._serialize_context(context),
                "prompt": user_prompt,
                "llm": {
                    "provider": "mock",
                    "model": "deterministic-template",
                    "used_fallback": True,
                    "error": None,
                },
            }

        try:
            llm_response = self.llm_provider.complete(
                system=SYSTEM_PROMPT,
                user=user_payload,
            )
            narrative = llm_response.text.strip()
            provider = llm_response.provider
            model = llm_response.model
            used_fallback = llm_response.used_fallback
            error: str | None = None
        except Exception as exc:  # pragma: no cover - network failures
            logger.exception(
                "LLM completion failed; falling back to deterministic template."
            )
            narrative = self._render_fallback_narrative(context)
            provider = "mock"
            model = "deterministic-template"
            used_fallback = True
            # Defence in depth: any error string that bubbles up here gets
            # scrubbed of plausible API-key shapes before being put in the
            # JSON response or rendered in the UI.
            error = _redact(str(exc), None)

        return {
            "scenario_id": scenario,
            "generated_at": datetime.now(UTC).isoformat(),
            "narrative": narrative,
            "context": self._serialize_context(context),
            "prompt": user_prompt,
            "llm": {
                "provider": provider,
                "model": model,
                "used_fallback": used_fallback,
                "error": error,
            },
        }

    @staticmethod
    def _should_use_deterministic_report(context: RAGContext) -> bool:
        """Avoid slow/fragile LLM calls when there is no retrieval context.

        If the only actionable signals are deterministic detector outputs
        (exact duplicate or PrincipesMarocPipeline flags), the LLM would not
        add new evidence and must not create risk. The deterministic narrative
        is faster and keeps the report tied to the existing flags.
        """
        moroccan_flags = (
            (context.moroccan_constants_summary or {}).get("flags") or []
        )
        return context.exact_duplicate or (
            len(context.passages) == 0 and bool(moroccan_flags)
        )

    # ---------- Context building ----------

    def _build_context(
        self, analysis: dict[str, Any], scenario_id: str
    ) -> RAGContext:
        plagiarism = analysis.get("plagiarism") or {}
        rag_report = analysis.get("rag_report") or {}
        document_stats = analysis.get("document_stats") or {}
        profanity = analysis.get("profanity") or {}
        adult_content = analysis.get("adult_content") or {}
        moroccan_constants = analysis.get("moroccan_constants") or {}

        risk_level = str(rag_report.get("risk_level") or "unknown")
        score = float(
            plagiarism.get("global_similarity_score") or plagiarism.get("score") or 0.0
        )
        score_pct = score * 100 if score <= 1 else score
        duplicate_analyses = [
            item
            for item in (plagiarism.get("duplicate_analyses") or [])
            if isinstance(item, dict)
        ]
        exact_duplicate = bool(
            plagiarism.get("exact_duplicate")
            or plagiarism.get("duplicate")
            or duplicate_analyses
        )
        duplicate_count = int(
            plagiarism.get("duplicate_count") or len(duplicate_analyses) or 0
        )

        passages, selection_stats = self._select_passages(plagiarism)

        # End-to-end RAG: if the plagiarism pipeline did not retain enough
        # passages (e.g. the threshold filtered everything out), re-query
        # Qdrant directly from the current document's chunks so the LLM
        # still has *some* semantic context to reason about.
        fallback_used = False
        if not exact_duplicate and len(passages) < self.candidate_pool_size:
            extra_passages, fallback_used = self._fallback_retrieve(
                analysis=analysis,
                scenario_id=scenario_id,
                already_seen=passages,
            )
            if extra_passages:
                passages = self._merge_passages(passages, extra_passages)

        # Optional rerank step: a wider cosine pool was collected above; ask
        # the LLM to re-score it by editorial relevance and keep the top
        # ``max_passages``. On any failure the original order survives.
        rerank_diagnostics: dict[str, Any] = {}
        if (
            settings.ADVANCED_RAG_RERANK_ENABLED
            and not exact_duplicate
            and len(passages) > self.max_passages
        ):
            passages, rerank_diagnostics = self._rerank_passages(
                passages, document_stats
            )

        retrieval_status, retrieval_reason, retrieval_diagnostics = (
            self._diagnose_retrieval(plagiarism=plagiarism, passages=passages)
        )
        retrieval_diagnostics.update(selection_stats)
        retrieval_diagnostics["max_passages"] = self.max_passages
        retrieval_diagnostics["candidate_pool_size"] = self.candidate_pool_size
        if fallback_used:
            retrieval_diagnostics["fallback_retrieval_used"] = True
        if rerank_diagnostics:
            retrieval_diagnostics["rerank"] = rerank_diagnostics

        document_summary = {
            "original_filename": document_stats.get("original_filename"),
            "stored_filename": document_stats.get("file_name"),
            "words_count": document_stats.get("words_count")
            or document_stats.get("word_count"),
            "chunks_count": document_stats.get("chunks_count")
            or document_stats.get("chunk_count"),
        }

        moderation_summary = {
            "profanity_score": float(profanity.get("profanity_score") or 0.0),
            "profanity_words": list(profanity.get("detected_words") or [])[:10],
            "adult_content_score": float(
                adult_content.get("adult_content_score") or 0.0
            ),
            "adult_risk_level": adult_content.get("risk_level"),
        }
        moroccan_constants_summary = self._build_moroccan_constants_summary(
            moroccan_constants
        )

        return RAGContext(
            scenario_id=scenario_id,
            risk_level=risk_level,
            similarity_score_pct=round(score_pct, 2),
            total_matches=int(plagiarism.get("total_matches") or len(passages)),
            total_sources=int(plagiarism.get("total_sources") or 0),
            passages=passages,
            exact_duplicate=exact_duplicate,
            duplicate_count=duplicate_count,
            duplicate_analyses=duplicate_analyses,
            document_summary=document_summary,
            moderation_summary=moderation_summary,
            moroccan_constants_summary=moroccan_constants_summary,
            retrieval_status=retrieval_status,
            retrieval_reason=retrieval_reason,
            retrieval_diagnostics=retrieval_diagnostics,
        )

    def _build_moroccan_constants_summary(
        self, moroccan_constants: Any
    ) -> dict[str, Any]:
        """Return detector-owned flags for RAG explanation.

        The RAG layer must not classify Moroccan-constants risk on its own.
        It only receives these deterministic flags plus their chunk evidence.
        """
        if not isinstance(moroccan_constants, dict):
            moroccan_constants = {}
        flags = [
            {
                "category": str(flag.get("category") or "unknown"),
                "category_label": self._moroccan_category_label(
                    str(flag.get("category") or "")
                ),
                "risk_level": str(flag.get("severity") or "faible"),
                "reason": str(flag.get("explanation") or ""),
                "chunk_index": flag.get("chunk_index"),
                "evidence": _truncate(flag.get("evidence"), self.MAX_EXCERPT_CHARS),
                "requires_human_review": True,
            }
            for flag in (moroccan_constants.get("flags") or [])
            if isinstance(flag, dict)
        ]
        return {
            "score": float(moroccan_constants.get("score") or 0.0),
            "risk_level": str(moroccan_constants.get("risk_level") or "faible"),
            "flags": flags[:8],
            "total_flags": len(flags),
            "has_flags": bool(flags),
        }

    @staticmethod
    def _moroccan_category_label(category: str) -> str:
        return {
            "islam": "Religion islamique moderee",
            "national_unity": "Unite nationale et integrite territoriale",
            "monarchy": "Monarchie constitutionnelle",
            "democratic_choice": "Choix democratique",
        }.get(category, category or "Categorie non disponible")

    def _rerank_passages(
        self,
        passages: list[RetrievedPassage],
        document_stats: dict[str, Any],
    ) -> tuple[list[RetrievedPassage], dict[str, Any]]:
        """Apply LLM rerank and trim to ``self.max_passages``.

        Returns ``(trimmed_passages, diagnostics)``. The diagnostics dict
        is empty when the rerank step fell back; that is the signal for
        the caller that the cosine ordering survived unchanged.
        """
        excerpts = [
            p.source_excerpt or p.current_excerpt or "" for p in passages
        ]
        summary = self._document_summary_for_rerank(passages, document_stats)
        result = self.reranker.rerank(
            document_summary=summary, candidates=excerpts
        )
        if result.used_fallback:
            trimmed = passages[: self.max_passages]
            for i, p in enumerate(trimmed, start=1):
                p.rank = i
            return trimmed, {
                "applied": False,
                "reason": result.parse_error or "fallback",
                "pool_size": len(passages),
            }

        ordered = [passages[i] for i in result.ordered_indexes]
        trimmed = ordered[: self.max_passages]
        for i, p in enumerate(trimmed, start=1):
            p.rank = i
        return trimmed, {
            "applied": True,
            "pool_size": len(passages),
            "kept": len(trimmed),
            "scores": {
                str(i): round(s, 2) for i, s in list(result.scores.items())[:20]
            },
        }

    @staticmethod
    def _document_summary_for_rerank(
        passages: list[RetrievedPassage],
        document_stats: dict[str, Any],
    ) -> str:
        """Build a short, self-contained summary for the rerank prompt.

        We don't have the full document text here, so we synthesise a
        compact view from the analysed-side excerpts (which are taken
        from the uploaded scenario). That gives the LLM enough signal to
        judge which candidate is most relevant.
        """
        filename = document_stats.get("original_filename") or "scénario inconnu"
        head = f"Fichier : {filename}."
        analysed = [p.current_excerpt for p in passages if p.current_excerpt][:3]
        if analysed:
            joined = " | ".join(a.replace("\n", " ") for a in analysed)
            return f"{head} Extraits représentatifs : {joined}"
        return head

    def _multi_query_extra_passages(
        self,
        *,
        document_excerpts: list[str],
        scenario_id: str,
        seen_signatures: set[str],
        start_rank: int,
        budget: int,
    ) -> list[RetrievedPassage]:
        """Run the LLM-driven multi-query retriever and convert hits.

        Failures (LLM/JSON/embedding/Qdrant) are swallowed and return ``[]``
        so the caller can fall through to the legacy chunks-as-queries leg
        without observable degradation.
        """
        if budget <= 0:
            return []
        try:
            result = self.multi_query_retriever.retrieve(
                document_excerpts=document_excerpts,
                exclude_scenario_id=scenario_id,
            )
        except Exception:
            logger.exception("Multi-query retrieval crashed; falling through.")
            return []
        if result.used_fallback or not result.merged_hits:
            return []

        passages: list[RetrievedPassage] = []
        for hit in result.merged_hits:
            if len(passages) >= budget:
                break
            payload = hit.get("payload") or {}
            hit_scenario = str(payload.get("scenario_id") or "")
            source_text = str(
                payload.get("chunk_text_display")
                or payload.get("chunk_text")
                or ""
            )
            signature = (
                f"{hit_scenario}::"
                f"{_normalize_signature(source_text)[:160]}"
            )
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            passages.append(
                self._to_passage(
                    rank=start_rank + len(passages),
                    match={
                        "matched_scenario_id": hit_scenario,
                        "original_filename": payload.get("original_filename"),
                        "filename": payload.get("stored_filename"),
                        "stored_filename": payload.get("stored_filename"),
                        "similarity_score": float(hit.get("score") or 0.0),
                        "matched_chunk_text_display": source_text,
                        "matched_chunk_text": payload.get("chunk_text"),
                        "chunk_text": hit.get("matched_via_query") or "",
                        "page_number": payload.get("page_number"),
                        "source_page_number": payload.get("page_number"),
                        "source_chunk_index": payload.get("chunk_index"),
                    },
                )
            )
        return passages

    def _fallback_retrieve(
        self,
        analysis: dict[str, Any],
        scenario_id: str,
        already_seen: list[RetrievedPassage],
    ) -> tuple[list[RetrievedPassage], bool]:
        """Re-query Qdrant directly from the current scenario's chunks.

        This is the "end-to-end RAG" leg: instead of relying on whatever the
        plagiarism filter kept, we embed a few representative chunks of the
        current document and ask Qdrant for the nearest neighbours. Anything
        coming back from the *same* scenario_id is ignored.
        """
        query_texts = self._extract_query_texts(analysis)
        if not query_texts:
            return [], False

        budget = max(self.candidate_pool_size - len(already_seen), 0)
        if budget == 0:
            return [], False

        seen_signatures = {
            f"{p.source_scenario_id}::{_normalize_signature(p.source_excerpt)[:160]}"
            for p in already_seen
        }
        extra: list[RetrievedPassage] = []

        # Multi-query retrieval (semantic query rewriting). When the LLM
        # successfully produces N rewritten queries we use those hits
        # *before* falling back to the legacy "longest-chunks-as-queries"
        # strategy. The two passes complement each other: rewritten queries
        # catch transpositions/reformulations, raw-chunk queries catch
        # near-verbatim copies.
        if settings.ADVANCED_RAG_MULTI_QUERY_ENABLED:
            extra = self._multi_query_extra_passages(
                document_excerpts=query_texts,
                scenario_id=scenario_id,
                seen_signatures=seen_signatures,
                start_rank=len(already_seen) + 1,
                budget=budget,
            )
            budget = max(
                self.candidate_pool_size - len(already_seen) - len(extra), 0
            )
            if budget == 0:
                return extra, True

        try:
            embeddings = self.embedding_service.generate_embeddings(
                query_texts, is_query=True
            )
        except Exception:
            logger.exception("Fallback retrieval: embedding step failed.")
            return extra, bool(extra)

        per_query_limit = max(budget, 3)
        for query_text, embedding in zip(query_texts, embeddings):
            if len(extra) >= budget:
                break
            try:
                hits = self.vector_service.search_similar_chunks(
                    embedding=embedding, limit=per_query_limit
                )
            except Exception:
                logger.exception("Fallback retrieval: Qdrant search failed.")
                return extra, bool(extra)

            for hit in hits:
                if len(extra) >= budget:
                    break
                payload = hit.get("payload") or {}
                hit_scenario = str(payload.get("scenario_id") or "")
                if hit_scenario == scenario_id:
                    continue
                source_text = str(
                    payload.get("chunk_text_display")
                    or payload.get("chunk_text")
                    or ""
                )
                signature = (
                    f"{hit_scenario}::"
                    f"{_normalize_signature(source_text)[:160]}"
                )
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                extra.append(
                    self._to_passage(
                        rank=len(already_seen) + len(extra) + 1,
                        match={
                            "matched_scenario_id": hit_scenario,
                            "original_filename": payload.get("original_filename"),
                            "filename": payload.get("stored_filename"),
                            "stored_filename": payload.get("stored_filename"),
                            "similarity_score": float(hit.get("score") or 0.0),
                            "matched_chunk_text_display": source_text,
                            "matched_chunk_text": payload.get("chunk_text"),
                            "chunk_text": query_text,
                            "page_number": payload.get("page_number"),
                            "source_page_number": payload.get("page_number"),
                            "source_chunk_index": payload.get("chunk_index"),
                        },
                    )
                )
        return extra, True

    @staticmethod
    def _merge_passages(
        primary: list[RetrievedPassage],
        extras: list[RetrievedPassage],
    ) -> list[RetrievedPassage]:
        merged = list(primary) + list(extras)
        merged.sort(key=lambda p: p.score_pct, reverse=True)
        for i, passage in enumerate(merged, start=1):
            passage.rank = i
        return merged

    def _extract_query_texts(self, analysis: dict[str, Any]) -> list[str]:
        """Pick a handful of chunks from the current analysis to use as queries.

        We bias toward the longest chunks (more semantic signal) and cap the
        number of queries so we don't hammer Qdrant.
        """
        candidates: list[str] = []
        document = analysis.get("document_chunks") or []
        if isinstance(document, list):
            for chunk in document:
                if isinstance(chunk, dict):
                    text = chunk.get("text_normalized") or chunk.get("text") or ""
                elif isinstance(chunk, str):
                    text = chunk
                else:
                    text = ""
                text = str(text).strip()
                if len(text.split()) >= 20:
                    candidates.append(text)

        if not candidates:
            # Fall back on the cleaned text stored on the analysis stats.
            stats = analysis.get("document_stats") or {}
            cleaned = str(stats.get("cleaned_text_preview") or "").strip()
            if cleaned:
                candidates.append(cleaned)

        if not candidates:
            return []

        candidates.sort(key=lambda t: len(t), reverse=True)
        max_queries = max(2, min(5, self.max_passages // 2))
        return candidates[:max_queries]

    def _diagnose_retrieval(
        self,
        plagiarism: dict[str, Any],
        passages: list[RetrievedPassage],
    ) -> tuple[str, str, dict[str, Any]]:
        """Explain why ``passages`` may be empty.

        The frontend uses this so a "0 passage(s) utilisé(s) comme contexte"
        line in the report can be qualified ("Qdrant indisponible" vs "rien
        n'a franchi le seuil de similarité" vs "corpus vide").
        """
        plagiarism_status = str(plagiarism.get("status") or "ok").lower()
        diagnostics_payload = plagiarism.get("diagnostics") or {}
        raw_qdrant_results = int(
            diagnostics_payload.get("raw_qdrant_results_count")
            or diagnostics_payload.get("raw_qdrant_matches")
            or 0
        )
        kept_matches = int(
            diagnostics_payload.get("kept_matches")
            or len(plagiarism.get("matches") or [])
        )
        threshold = float(
            plagiarism.get("similarity_threshold")
            or settings.PLAGIARISM_SIMILARITY_THRESHOLD
        )

        diag: dict[str, Any] = {
            "plagiarism_status": plagiarism_status,
            "raw_qdrant_results": raw_qdrant_results,
            "kept_matches": kept_matches,
            "similarity_threshold": threshold,
            "passages_retained": len(passages),
        }

        if passages:
            return "ok", "", diag

        if plagiarism_status == "unavailable":
            return (
                "qdrant_unavailable",
                "Qdrant est indisponible : aucune lecture sémantique n'a "
                "pu être effectuée pour ce scénario.",
                diag,
            )

        if raw_qdrant_results == 0:
            return (
                "corpus_empty",
                "Aucun chunk candidat n'a été retourné par Qdrant : le "
                "corpus indexé est vide ou ne contient que des chunks du "
                "scénario courant.",
                diag,
            )

        if kept_matches == 0:
            return (
                "below_threshold",
                f"{raw_qdrant_results} candidat(s) retourné(s) par Qdrant, "
                f"mais aucun n'a franchi le seuil de similarité "
                f"({threshold:.2f}).",
                diag,
            )

        return (
            "no_match",
            "Aucun passage similaire significatif n'a été retenu après "
            "filtrage.",
            diag,
        )

    def _select_passages(
        self, plagiarism: dict[str, Any]
    ) -> tuple[list[RetrievedPassage], dict[str, int]]:
        """Pick the top passages plus a tiny stats dict for diagnostics."""
        sources = plagiarism.get("plagiarism_sources") or []
        flat_matches = plagiarism.get("matches") or []

        candidates: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()

        # Iterate sources first so we get diversity across documents.
        if isinstance(sources, list) and sources:
            for source in sources:
                if not isinstance(source, dict):
                    continue
                for match in source.get("matches", []) or []:
                    if not isinstance(match, dict):
                        continue
                    candidates.append(
                        self._enrich_match_with_source(match, source)
                    )

        # Fall back / complete with the flat displayed matches.
        for match in flat_matches if isinstance(flat_matches, list) else []:
            if isinstance(match, dict):
                candidates.append(match)

        stats = {
            "candidates_in": len(candidates),
            "exact_duplicate_skipped": 0,
            "dedup_collapsed": 0,
        }

        ranked: list[RetrievedPassage] = []
        for raw in candidates:
            if self._is_exact_duplicate_match(raw):
                stats["exact_duplicate_skipped"] += 1
                continue
            signature = self._dedup_signature(raw)
            if signature in seen_signatures:
                stats["dedup_collapsed"] += 1
                continue
            seen_signatures.add(signature)
            ranked.append(self._to_passage(len(ranked) + 1, raw))
            if len(ranked) >= self.candidate_pool_size:
                break

        # Sort by score desc for the prompt — keeps the strongest signals on top.
        ranked.sort(key=lambda p: p.score_pct, reverse=True)
        for i, passage in enumerate(ranked, start=1):
            passage.rank = i
        stats["candidates_out"] = len(ranked)
        return ranked, stats

    @staticmethod
    def _is_exact_duplicate_match(match: dict[str, Any]) -> bool:
        return (
            match.get("duplicate") is True
            or match.get("exact_duplicate") is True
            or str(match.get("match_type") or "").lower() == "exact_duplicate"
        )

    @staticmethod
    def _enrich_match_with_source(
        match: dict[str, Any], source: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(match)
        # Source-level metadata is authoritative — it identifies the parent
        # document even if the inner match dict was built before grouping.
        if source.get("original_filename"):
            merged["original_filename"] = source["original_filename"]
            merged["filename"] = source["original_filename"]
        if source.get("stored_filename"):
            merged["stored_filename"] = source["stored_filename"]
        if source.get("source_scenario_id"):
            merged["matched_scenario_id"] = source["source_scenario_id"]
        return merged

    def _dedup_signature(self, match: dict[str, Any]) -> str:
        """Build a collision-resistant signature for a plagiarism match.

        The previous implementation took the first 160 characters of the
        source chunk's text. On documents with a repeated header
        (templated scenario formats), every chunk shares the same first
        few hundred characters so distinct passages collapsed into a
        single signature, capping the prompt at 3-4 passages instead of
        the configured 6.

        The new signature carries two extra elements that are stable for
        a given match but vary across chunks:

        - ``position``: chunk index or page number of the *source*
          passage. Two different positions can never collapse, even when
          the surrounding text is byte-identical.
        - A 200-char slice taken from the *middle* of the normalized
          text, which skips the boilerplate prefix while remaining wide
          enough to keep deterministic dedup of genuinely identical
          passages.
        """
        source = (
            match.get("matched_scenario_id")
            or match.get("stored_filename")
            or match.get("filename")
            or "unknown-source"
        )
        position = (
            match.get("source_chunk_index")
            if match.get("source_chunk_index") is not None
            else match.get("source_chunk_id")
            or match.get("source_page_number")
            or match.get("page_number")
            or "?"
        )
        text = str(
            match.get("matched_chunk_text_display")
            or match.get("matched_chunk_text")
            or match.get("snippet")
            or match.get("overlap_text")
            or ""
        )
        normalized = _normalize_signature(text)
        if len(normalized) <= 200:
            mid_slice = normalized
        else:
            # Skip the first quarter (boilerplate header) and take a
            # 200-char window from the middle of the chunk.
            start = len(normalized) // 4
            mid_slice = normalized[start : start + 200]
        return f"{source}::pos={position}::{mid_slice}"

    def _to_passage(self, rank: int, match: dict[str, Any]) -> RetrievedPassage:
        score = float(
            match.get("similarity_score")
            or match.get("similarity")
            or match.get("score")
            or 0.0
        )
        score_pct = round(score * 100 if score <= 1 else score, 2)
        current_position = _format_position(
            match.get("current_page_number")
            or match.get("current_chunk_index")
            or match.get("chunk_index")
        )
        source_position = _format_position(
            match.get("source_page_number")
            or match.get("source_chunk_index")
            or match.get("page_number")
        )
        current_excerpt = _truncate(
            match.get("chunk_text"), self.MAX_EXCERPT_CHARS
        )
        source_excerpt = _truncate(
            match.get("matched_chunk_text_display")
            or match.get("matched_chunk_text")
            or match.get("snippet"),
            self.MAX_EXCERPT_CHARS,
        )
        overlap = match.get("overlap_text")
        if isinstance(overlap, str):
            overlap = overlap.strip() or None

        return RetrievedPassage(
            rank=rank,
            source_filename=_clean_str(
                match.get("original_filename") or match.get("filename")
            )
            or "non disponible",
            source_scenario_id=_clean_str(match.get("matched_scenario_id"))
            or "non disponible",
            score_pct=score_pct,
            current_position=current_position,
            source_position=source_position,
            current_excerpt=current_excerpt,
            source_excerpt=source_excerpt,
            overlap=overlap if isinstance(overlap, str) else None,
            grouped_copies=int(match.get("grouped_copies") or 0),
        )

    # ---------- Prompt rendering ----------

    def _render_user_prompt(self, context: RAGContext) -> str:
        no_context = len(context.passages) == 0
        header = self._render_prompt_header(context)
        moderation_section = self._render_moderation_section(context)
        moroccan_constants_section = self._render_moroccan_constants_section(
            context
        )

        if context.exact_duplicate:
            return self._render_exact_duplicate_prompt(
                context=context,
                header=header,
                moderation_section=moderation_section,
                moroccan_constants_section=moroccan_constants_section,
            )

        if no_context:
            return f"""{header}

# Passages similaires retrouvés

(Aucun passage similaire significatif n'a été retrouvé. Aucune comparaison \
chunk-à-chunk n'est disponible pour ce scénario.)

# Signaux de modération

{moderation_section}

# Constantes nationales marocaines

{moroccan_constants_section}

# Mission (cas SANS passage similaire)

Produis un rapport explicatif structuré en français, avec EXACTEMENT ces \
sections dans cet ordre :

1. **Synthèse globale** — 2 à 4 phrases : indiquer clairement qu'aucun \
passage similaire n'a été retrouvé dans le corpus déjà indexé, et que le \
scénario apparaît original au regard des documents comparés.
2. **Interprétation du score** — 2 à 3 phrases : expliquer ce que signifie \
un score global de similarité de {context.similarity_score_pct:.2f}% en \
l'absence de correspondances chunk-à-chunk.
3. **Limites de l'analyse** — 2 à 4 phrases : rappeler que l'absence de \
correspondance ne garantit pas l'originalité (le corpus indexé peut être \
incomplet, certaines reformulations échappent à la détection automatique, \
le seuil de similarité peut être trop strict).
4. **Actions recommandées** — liste numérotée de 2 à 4 actions concrètes, \
adaptées au cas « aucun signal de plagiat » (par ex. vérification éditoriale \
standard, archivage du rapport, élargissement éventuel du corpus).
5. **Conclusion** — 1 à 2 phrases pour clore.

Contraintes STRICTES :
- Aucun passage similaire n'est fourni ci-dessus : NE GÉNÈRE PAS de \
section listant les passages, et NE PRODUIS PAS la rubrique habituelle \
d'analyse détaillée par passage.
- N'INVENTE PAS de Passage 1, Passage 2, etc.
- N'INVENTE PAS de noms de documents source ni de scores fictifs.
- Pour les constantes nationales marocaines, explique uniquement les flags
fournis dans la section dediee. Si aucun flag n'est fourni, ecris exactement :
"Aucune atteinte evidente aux constantes nationales marocaines n'a ete detectee."
- Ne classifie jamais un passage sur les constantes nationales sans evidence
issue d'un chunk.
- Reste factuel, neutre, et utilise UNIQUEMENT les informations fournies \
ci-dessus.
- Pas plus de 400 mots au total.
"""

        passage_blocks: list[str] = []
        for p in context.passages:
            block = [
                f"### Passage {p.rank} — score {p.score_pct:.2f}%",
                f"- Document source : {p.source_filename}",
                f"- Scénario source : {p.source_scenario_id}",
                f"- Position : {p.current_position} (analyse) ↔ "
                f"{p.source_position} (source)",
            ]
            if p.grouped_copies > 1:
                block.append(f"- Copies similaires regroupées : {p.grouped_copies}")
            if p.overlap:
                block.append(f"- Overlap brut détecté : « {p.overlap} »")
            block.append("- Extrait du scénario analysé :")
            block.append(f"  > {p.current_excerpt or '(non disponible)'}")
            block.append("- Extrait du document source :")
            block.append(f"  > {p.source_excerpt or '(non disponible)'}")
            passage_blocks.append("\n".join(block))

        passages_section = "\n\n".join(passage_blocks)

        return f"""{header}

# Passages similaires retrouvés (top {len(context.passages)})

{passages_section}

# Signaux de modération

{moderation_section}

# Constantes nationales marocaines

{moroccan_constants_section}

# Mission

Produis un rapport explicatif structuré en français, avec ces sections \
exactement dans cet ordre :

1. **Synthèse globale** — 3 à 5 phrases : qu'a révélé l'analyse, quel \
risque, quelle est la nature des passages similaires.
2. **Analyse passage par passage** — pour chaque passage fourni ci-dessus, \
décris en 2-3 phrases ce qui est similaire et donne une hypothèse \
plausible (réutilisation volontaire, citation, coïncidence stylistique, …).
3. **Conséquences éditoriales** — quels risques concrets pour la \
production / le comité de lecture.
4. **Actions recommandées** — liste numérotée de 3 à 5 actions concrètes.
5. **Conclusion** — 2 phrases pour clore.

Contraintes :
- N'invente aucun fait absent du contexte.
- Cite les passages par leur numéro (« Passage 2 »).
- Pour les constantes nationales marocaines, explique uniquement les flags
fournis dans la section dediee. Toute explication doit citer ou resumer
l'evidence du chunk correspondant.
- Si aucun flag de constantes nationales n'est fourni, ecris exactement :
"Aucune atteinte evidente aux constantes nationales marocaines n'a ete detectee."
- Ne laisse jamais le RAG inventer un risque absent ou classifier un passage
sans evidence issue des chunks.
- Reste factuel et neutre.
- Pas plus de 600 mots au total.
"""

    def _render_exact_duplicate_prompt(
        self,
        *,
        context: RAGContext,
        header: str,
        moderation_section: str,
        moroccan_constants_section: str,
    ) -> str:
        duplicate_lines = [
            "- Doublon exact detecte : oui",
            f"- Nombre d'analyses identiques connues : {context.duplicate_count}",
            "- Interpretation du score : le score de 100% correspond a une "
            "duplication exacte interne dans l'historique.",
        ]
        for index, duplicate in enumerate(context.duplicate_analyses[:5], start=1):
            duplicate_lines.append(
                "- Analyse identique "
                f"{index} : scenario={duplicate.get('scenario_id') or 'non disponible'}, "
                f"fichier={duplicate.get('original_filename') or duplicate.get('stored_filename') or 'non disponible'}, "
                f"date={duplicate.get('created_at') or 'non disponible'}"
            )
        duplicates_section = "\n".join(duplicate_lines)

        if context.passages:
            passage_blocks: list[str] = []
            for p in context.passages:
                passage_blocks.append(
                    "\n".join(
                        [
                            f"### Passage {p.rank} - score {p.score_pct:.2f}%",
                            f"- Document source externe : {p.source_filename}",
                            f"- Scenario source : {p.source_scenario_id}",
                            "- Extrait du scenario analyse : "
                            f"{p.current_excerpt or '(non disponible)'}",
                            "- Extrait du document source : "
                            f"{p.source_excerpt or '(non disponible)'}",
                        ]
                    )
                )
            partial_section = "\n\n".join(passage_blocks)
        else:
            partial_section = (
                "Aucun passage similaire significatif externe n'a ete retenu "
                "comme plagiat partiel."
            )

        return f"""{header}

# Signal prioritaire : doublon exact interne

{duplicates_section}

# Passages similaires partiels non lies au doublon exact

{partial_section}

# Signaux de moderation

{moderation_section}

# Constantes nationales marocaines

{moroccan_constants_section}

# Mission prioritaire : DOUBLON EXACT

Produis un rapport explicatif structure en francais, avec EXACTEMENT ces
sections dans cet ordre :

1. **Synthese globale** - expliquer que le risque est principalement du au
doublon exact deja present dans l'historique. Utiliser la formulation
"principalement du a un doublon exact interne" ou une formulation equivalente
grammaticalement correcte.
2. **Analyse de duplication** - expliquer combien de fois le document a deja
ete analyse si l'information est disponible, et dire que le score de 100%
correspond a une duplication interne. Les anciennes analyses identiques sont
des entrees d'historique administratif : ne pas deduire qu'elles sont "les
memes que le scenario original" et ne pas leur attribuer un contenu non fourni.
3. **Analyse des similarites partielles** - analyser uniquement les passages
externes explicitement fournis ci-dessus. Si aucun passage externe n'est
fourni, ecrire exactement : "Aucun passage similaire significatif externe
n'a ete retenu comme plagiat partiel."
4. **Analyse de moderation** - mentionner les scores de vulgarite, contenu
adulte ou autres signaux disponibles, et rappeler que les mots detectes
doivent etre relus dans leur contexte narratif.
5. **Analyse des constantes nationales marocaines** - expliquer uniquement les
flags fournis par PrincipesMarocPipeline. Pour chaque flag fourni, indiquer la
constante concernee, le niveau de risque, la raison de l'alerte et si une
verification humaine est necessaire. Si aucun flag n'est fourni, ecrire
exactement : "Aucune atteinte evidente aux constantes nationales marocaines
n'a ete detectee."
6. **Actions recommandees** - recommander de verifier s'il s'agit d'une
soumission repetee, d'un test ou d'un doublon administratif ; conserver une
trace de la decision finale ; relire les passages signales par la moderation
si necessaire.
7. **Conclusion** - conclure que le risque HIGH vient principalement du
doublon exact interne, pas d'un plagiat partiel confirme, sauf preuve
contraire dans les donnees.

Hierarchie d'interpretation OBLIGATOIRE :
1. Si exact_duplicate=true ou si les donnees indiquent un doublon exact deja
analyse, ce signal domine l'interpretation.
2. Ensuite seulement, analyser les passages similaires significatifs non lies
au doublon exact.
3. Ensuite, analyser la moderation : vulgarite, violence, contenu adulte, etc.
4. Enfin, produire les recommandations.

Contraintes STRICTES :
- Ne jamais inventer de sources.
- Ne jamais transformer un doublon exact interne en accusation de plagiat.
- Ne jamais affirmer une intention volontaire.
- Ne jamais presenter les anciennes analyses identiques comme plusieurs
documents sources de plagiat.
- Ne pas ecrire "reutilisation volontaire", "citation non autorisee",
"plagiat confirme", "forte similitude avec plusieurs documents",
"risque eleve de plagiat" ou formulation accusatoire equivalente, sauf si des
passages similaires significatifs non lies au doublon exact sont explicitement
fournis.
- Preciser que le score de 100% ne constitue pas automatiquement une preuve de
plagiat partiel.
- Pour les constantes nationales marocaines, ne jamais classifier un passage
sans evidence issue d'un chunk. Le RAG explique les flags detectes ; il ne les
detecte pas.
- Utiliser un ton neutre, administratif et editorial.
- Ecrire en francais professionnel. Ne pas utiliser l'adjectif franglais derive
de "partial" : les formulations correctes sont "plagiat partiel" et
"similarite partielle".
- Ne pas ajouter de titre general avant "Synthese globale" : commencer
directement par la premiere section demandee.
- Pas plus de 650 mots au total.
"""

    def _render_prompt_header(self, context: RAGContext) -> str:
        stats = context.document_summary
        return (
            "# Contexte de l'analyse\n\n"
            f"- Scénario analysé : {context.scenario_id}\n"
            f"- Nom du fichier original : "
            f"{stats.get('original_filename') or 'non disponible'}\n"
            f"- Nombre de mots : "
            f"{stats.get('words_count') or 'non disponible'}\n"
            f"- Nombre de chunks : "
            f"{stats.get('chunks_count') or 'non disponible'}\n"
            f"- Niveau de risque global : {context.risk_level.upper()}\n"
            f"- Score global de similarité : "
            f"{context.similarity_score_pct:.2f}%\n"
            f"- Passages similaires détectés au total : {context.total_matches}\n"
            f"- Documents source distincts : {context.total_sources}\n"
            f"- Doublon exact interne : "
            f"{'oui' if context.exact_duplicate else 'non'}\n"
            f"- Nombre d'analyses identiques : {context.duplicate_count}"
        )

    def _render_moderation_section(self, context: RAGContext) -> str:
        moderation_lines: list[str] = []
        prof_score = context.moderation_summary.get("profanity_score") or 0.0
        adult_score = context.moderation_summary.get("adult_content_score") or 0.0
        if prof_score > 0:
            words = ", ".join(
                context.moderation_summary.get("profanity_words", [])
            ) or "(aucun mot listé)"
            moderation_lines.append(
                f"- Score de vulgarité : {prof_score:.2f}/100 — mots : {words}"
            )
        if adult_score > 0:
            moderation_lines.append(
                f"- Score de contenu adulte : {adult_score:.2f}/100 "
                f"(niveau : {context.moderation_summary.get('adult_risk_level')})"
            )
        return (
            "\n".join(moderation_lines)
            if moderation_lines
            else "Aucun signal de modération significatif."
        )

    def _render_moroccan_constants_section(self, context: RAGContext) -> str:
        summary = context.moroccan_constants_summary or {}
        flags = summary.get("flags") or []
        if not flags:
            return (
                "Aucune atteinte evidente aux constantes nationales marocaines "
                "n'a ete detectee."
            )

        lines = [
            f"- Score PrincipesMarocPipeline : {float(summary.get('score') or 0.0):.2f}",
            f"- Niveau global PrincipesMarocPipeline : {summary.get('risk_level') or 'faible'}",
            f"- Nombre total de flags : {summary.get('total_flags') or len(flags)}",
        ]
        for index, flag in enumerate(flags, start=1):
            lines.extend(
                [
                    f"### Flag {index}",
                    f"- Constante concernee : {flag.get('category_label') or flag.get('category')}",
                    f"- Niveau de risque : {flag.get('risk_level')}",
                    f"- Chunk source : {flag.get('chunk_index')}",
                    f"- Raison de l'alerte : {flag.get('reason') or 'non disponible'}",
                    "- Verification humaine necessaire : oui",
                    f"- Evidence extraite du PDF : {flag.get('evidence') or 'non disponible'}",
                ]
            )
        lines.append(
            "Important : ces flags proviennent de PrincipesMarocPipeline. "
            "Le RAG doit seulement les expliquer, sans ajouter de nouveau risque."
        )
        return "\n".join(lines)

    def _render_fallback_moroccan_constants_lines(
        self, context: RAGContext
    ) -> list[str]:
        summary = context.moroccan_constants_summary or {}
        flags = summary.get("flags") or []
        lines = ["", "## Constantes nationales marocaines"]
        if not flags:
            lines.append(
                "Aucune atteinte evidente aux constantes nationales marocaines "
                "n'a ete detectee."
            )
            return lines

        lines.append(
            "Les alertes ci-dessous proviennent de PrincipesMarocPipeline. "
            "Elles constituent des signaux d'aide a l'analyse et doivent etre "
            "verifiees humainement."
        )
        for flag in flags:
            lines.append(
                "- "
                f"Constante : {flag.get('category_label') or flag.get('category')} ; "
                f"risque : {flag.get('risk_level')} ; "
                f"chunk : {flag.get('chunk_index')} ; "
                f"raison : {flag.get('reason') or 'non disponible'} ; "
                f"evidence : {flag.get('evidence') or 'non disponible'}"
            )
        return lines

    # ---------- Fallback narrative (mock provider) ----------

    def _render_fallback_no_context(self, context: RAGContext) -> str:
        """Narrative used when zero passages are available.

        Strictly mirrors the structure asked of the LLM in the no-context
        prompt: Synthèse globale → Interprétation du score → Limites de
        l'analyse → Actions recommandées → Conclusion. NO "Analyse passage
        par passage" section is produced so the rendered report cannot
        invent passages that do not exist.
        """
        score_str = f"{context.similarity_score_pct:.2f}%"
        lines: list[str] = []

        lines.append("## Synthèse globale")
        lines.append(
            "Aucun passage similaire significatif n'a été retrouvé dans le "
            "corpus indexé. Le scénario apparaît original au regard des "
            "documents déjà analysés."
        )

        lines.append("")
        lines.append("## Interprétation du score")
        lines.append(
            f"Le score global de similarité affiché ({score_str}) reflète "
            "uniquement le bruit résiduel des comparaisons vectorielles : "
            "aucune correspondance chunk-à-chunk n'a franchi le seuil de "
            "détection. Le niveau de risque associé est "
            f"{context.risk_level.upper()}."
        )

        lines.append("")
        lines.append("## Limites de l'analyse")
        lines.append(
            "L'absence de correspondance ne garantit pas l'originalité "
            "absolue du scénario : (1) le corpus indexé peut être incomplet "
            "ou ne pas couvrir le sous-genre du document ; (2) une "
            "reformulation profonde, une traduction ou un fort changement "
            "de style peuvent échapper à la détection automatique ; (3) le "
            "seuil de similarité retenu peut écarter des passages "
            "faiblement similaires."
        )

        lines.append("")
        lines.extend(self._render_fallback_moroccan_constants_lines(context))

        lines.append("## Actions recommandées")
        actions: list[str] = [
            "Procéder à une vérification éditoriale standard du scénario.",
            "Archiver le présent rapport dans l'historique d'analyse pour "
            "traçabilité.",
        ]
        if context.total_sources == 0:
            actions.append(
                "Envisager d'enrichir le corpus de référence si la base "
                "indexée est encore restreinte."
            )
        for i, action in enumerate(actions, start=1):
            lines.append(f"{i}. {action}")

        lines.append("")
        lines.append("## Conclusion")
        lines.append(
            "Aucun signal de plagiat n'est remonté par l'analyse vectorielle. "
            "Le scénario peut suivre le circuit éditorial habituel, sous "
            "réserve des limites mentionnées ci-dessus."
        )
        return "\n".join(lines).strip()

    def _render_fallback_narrative(self, context: RAGContext) -> str:
        """Deterministic, polished narrative used when no LLM is reachable."""
        if context.exact_duplicate:
            return self._render_fallback_exact_duplicate(context)
        if len(context.passages) == 0:
            return self._render_fallback_no_context(context)

        lines: list[str] = []
        lines.append("## Synthèse globale")
        lines.append(
            f"L'analyse a retrouvé {context.total_matches} passage(s) "
            f"similaire(s) dans {context.total_sources} document(s) source. "
            f"Le score global de similarité s'établit à "
            f"{context.similarity_score_pct:.2f}%, ce qui place le "
            f"scénario au niveau de risque {context.risk_level.upper()}."
        )
        lines.append(
            "Les extraits comparés ci-dessous montrent les passages qui "
            "ressortent en tête après déduplication par document source."
        )

        lines.append("")
        lines.append("## Analyse passage par passage")
        for p in context.passages:
            lines.append(
                f"- **Passage {p.rank}** — score {p.score_pct:.2f}% — "
                f"source : {p.source_filename}. "
                f"Position dans l'analyse : {p.current_position} ; "
                f"position dans la source : {p.source_position}."
            )
            if p.overlap:
                lines.append(
                    f"  Le segment commun le plus marquant est : "
                    f"« {_truncate(p.overlap, 240)} »."
                )
            else:
                lines.append(
                    "  Pas de séquence verbatim isolée — la similarité "
                    "provient probablement d'une reformulation proche."
                )

        lines.append("")
        lines.append("## Conséquences éditoriales")
        if context.risk_level.lower() == "high":
            lines.append(
                "Risque élevé : la similarité documentée justifie une "
                "vérification manuelle par le comité de lecture avant toute "
                "validation. Une réécriture des passages identifiés est à "
                "envisager si la source n'est pas correctement créditée."
            )
        elif context.risk_level.lower() == "medium":
            lines.append(
                "Risque modéré : les passages détectés méritent d'être "
                "vérifiés en contexte. Si les similarités proviennent de "
                "tournures usuelles, aucune action structurelle n'est "
                "nécessaire ; sinon, prévoir une reformulation."
            )
        else:
            lines.append(
                "Risque faible : pas de conséquence éditoriale particulière. "
                "L'analyse documentaire peut servir de trace dans le dossier."
            )

        lines.append("")
        lines.extend(self._render_fallback_moroccan_constants_lines(context))

        lines.append("## Actions recommandées")
        actions: list[str] = []
        if context.passages:
            actions.append(
                "Lire chaque passage signalé dans le rapport et le comparer "
                "à l'extrait source associé."
            )
        if context.similarity_score_pct >= 75:
            actions.append(
                "Demander à l'auteur une justification écrite des passages "
                "à haute similarité (citation, hommage, coïncidence)."
            )
        elif context.similarity_score_pct >= 40:
            actions.append(
                "Vérifier la nature des passages similaires (citations, "
                "tournures stylistiques communes, réutilisation involontaire)."
            )
        if context.moderation_summary.get("profanity_score", 0) > 0:
            actions.append(
                "Vérifier que les passages marqués comme vulgaires "
                "correspondent à des choix éditoriaux assumés."
            )
        actions.append(
            "Conserver le rapport généré dans l'historique de l'analyse "
            "pour traçabilité."
        )
        for i, action in enumerate(actions, start=1):
            lines.append(f"{i}. {action}")

        lines.append("")
        lines.append("## Conclusion")
        if context.risk_level.lower() == "high":
            lines.append(
                "Le scénario doit être revu manuellement avant validation. "
                "Le rapport fournit l'ensemble des éléments factuels "
                "nécessaires à la décision."
            )
        elif context.risk_level.lower() == "medium":
            lines.append(
                "Le scénario présente des signaux modérés qui justifient une "
                "lecture attentive des passages signalés avant validation."
            )
        else:
            lines.append(
                "Le scénario ne présente pas de signal critique. Le rapport "
                "peut être archivé en l'état."
            )

        return "\n".join(lines).strip()

    def _render_fallback_exact_duplicate(self, context: RAGContext) -> str:
        score_str = f"{context.similarity_score_pct:.2f}%"
        count_text = (
            f"{context.duplicate_count} fois"
            if context.duplicate_count
            else "au moins une fois"
        )
        lines: list[str] = []

        lines.append("## Synthese globale")
        lines.append(
            "Le niveau de risque HIGH est principalement lie a un doublon "
            "exact interne deja present dans l'historique. Le document "
            f"correspond a un fichier deja analyse {count_text}, et le score "
            f"de similarite de {score_str} doit etre interprete comme une "
            "duplication exacte interne."
        )

        lines.append("")
        lines.append("## Analyse de duplication")
        lines.append(
            "Le score de 100% indique que le fichier ou son texte nettoye "
            "correspond a une analyse anterieure. Ce signal sert d'abord a "
            "identifier une soumission repetee, un test ou un doublon "
            "administratif ; il ne constitue pas automatiquement une preuve "
            "de plagiat partiel."
        )

        lines.append("")
        lines.append("## Analyse des similarites partielles")
        if context.passages:
            for p in context.passages:
                lines.append(
                    f"- Passage {p.rank} : similarite {p.score_pct:.2f}% avec "
                    f"{p.source_filename}. Ce passage doit etre verifie comme "
                    "similarite partielle distincte du doublon exact."
                )
        else:
            lines.append(
                "Aucun passage similaire significatif externe n'a ete retenu "
                "comme plagiat partiel."
            )

        lines.append("")
        lines.append("## Analyse de moderation")
        prof_score = float(context.moderation_summary.get("profanity_score") or 0.0)
        adult_score = float(
            context.moderation_summary.get("adult_content_score") or 0.0
        )
        if prof_score or adult_score:
            lines.append(
                f"Score de vulgarite : {prof_score:.2f}/100. "
                f"Score de contenu adulte : {adult_score:.2f}/100. "
                "Les mots ou termes detectes doivent etre relus dans leur "
                "contexte narratif avant decision."
            )
        else:
            lines.append(
                "Aucun signal de moderation significatif n'est remonte. Les "
                "eventuels termes detectes dans d'autres vues doivent rester "
                "interpretes dans leur contexte narratif."
            )

        lines.append("")
        lines.extend(self._render_fallback_moroccan_constants_lines(context))

        lines.append("## Actions recommandees")
        actions = [
            "Verifier s'il s'agit d'une soumission repetee, d'un test ou d'un doublon administratif.",
            "Conserver une trace de la decision finale dans l'historique de l'analyse.",
            "Relire les passages signales par la moderation si necessaire.",
        ]
        for index, action in enumerate(actions, start=1):
            lines.append(f"{index}. {action}")

        lines.append("")
        lines.append("## Conclusion")
        lines.append(
            "Le risque HIGH vient principalement du doublon exact interne, "
            "pas d'un plagiat partiel confirme. Une verification "
            "administrative ou editoriale doit determiner pourquoi le meme "
            "fichier a ete analyse plusieurs fois."
        )
        return "\n".join(lines).strip()

    # ---------- Serialisation ----------

    @staticmethod
    def _serialize_context(context: RAGContext) -> dict[str, Any]:
        return {
            "scenario_id": context.scenario_id,
            "risk_level": context.risk_level,
            "similarity_score_pct": context.similarity_score_pct,
            "total_matches": context.total_matches,
            "total_sources": context.total_sources,
            "exact_duplicate": context.exact_duplicate,
            "duplicate_count": context.duplicate_count,
            "duplicate_analyses": context.duplicate_analyses,
            "document_summary": context.document_summary,
            "moderation_summary": context.moderation_summary,
            "moroccan_constants_summary": context.moroccan_constants_summary,
            "retrieval_status": context.retrieval_status,
            "retrieval_reason": context.retrieval_reason,
            "retrieval_diagnostics": context.retrieval_diagnostics,
            "passages": [
                {
                    "rank": p.rank,
                    "source_filename": p.source_filename,
                    "source_scenario_id": p.source_scenario_id,
                    "score_pct": p.score_pct,
                    "current_position": p.current_position,
                    "source_position": p.source_position,
                    "current_excerpt": p.current_excerpt,
                    "source_excerpt": p.source_excerpt,
                    "overlap": p.overlap,
                    "grouped_copies": p.grouped_copies,
                }
                for p in context.passages
            ],
        }


# ---------- Helpers ----------


_WHITESPACE_RE = re.compile(r"\s+")


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "null"} else text


def _truncate(value: Any, max_length: int) -> str:
    if value is None:
        return ""
    text = _WHITESPACE_RE.sub(" ", str(value)).strip()
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "…"


def _normalize_signature(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).casefold()
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def _format_position(raw: Any) -> str:
    if raw is None or raw == "":
        return "—"
    return str(raw)

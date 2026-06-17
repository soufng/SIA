"""Plagiarism pipeline.

Orchestrates the full plagiarism detection step on a ``DocumentContext``:

1. Local similarity (hash + shingles, MongoDB history).
2. Vector similarity (Qdrant embeddings).
3. Merge + dedupe + aggregate matches into the display-ready shape.
4. Strict-similarity verdict (used by the renewal workflow).
5. Store the document's chunks in Qdrant for future analyses.

All match-aggregation helpers that used to live on ``AnalysisService``
have been moved here — they are intrinsic to plagiarism, not to the
overall orchestration.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.core.config import settings
from backend.services.embedding_service import EmbeddingService
from backend.services.local_similarity_service import LocalSimilarityService
from backend.services.minhash_plagiarism_service import MinHashPlagiarismService
from backend.services.plagiarism_service import PlagiarismService
from backend.services.strict_similarity_service import StrictSimilarityService
from backend.services.vector_service import VectorService
from backend.utils.composite_scoring import (
    format_percent,
)


if TYPE_CHECKING:
    from backend.pipelines.document_pipeline import DocumentContext


logger = logging.getLogger(__name__)


@dataclass
class PlagiarismOutcome:
    plagiarism_result: dict[str, Any]
    strict_match: dict[str, Any]
    vector_available: bool


class PlagiarismPipeline:
    """End-to-end plagiarism detection on a document context."""

    # Display limits for the aggregated plagiarism block.
    MAX_SOURCES_DISPLAYED = settings.PLAGIARISM_MAX_SOURCES_DISPLAYED
    MAX_MATCHES_PER_SOURCE = settings.PLAGIARISM_MAX_MATCHES_PER_SOURCE
    MAX_TOTAL_MATCHES_DISPLAYED = settings.PLAGIARISM_MAX_TOTAL_MATCHES_DISPLAYED

    LOW_INFORMATION_WORDS = {
        "the", "and", "for", "with", "that", "this", "une", "des", "les",
        "pour", "dans", "avec", "cette", "ligne", "texte", "page", "test",
        "non", "commun", "remplissage", "confidentiel", "confidentielle",
        "document", "scenario", "scénario", "copyright", "entete", "en-tete",
        "footer", "header",
    }
    OVERLAP_TEXT_FIELDS = (
        "common_text",
        "overlap_text",
        "highlighted_text",
        "best_overlap",
        "matched_text",
    )

    def __init__(
        self,
        local_similarity_service: LocalSimilarityService,
        plagiarism_service: PlagiarismService,
        strict_similarity_service: StrictSimilarityService,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
        minhash_service: MinHashPlagiarismService | None = None,
    ) -> None:
        self.local_similarity_service = local_similarity_service
        self.plagiarism_service = plagiarism_service
        self.strict_similarity_service = strict_similarity_service
        self.embedding_service = embedding_service
        self.vector_service = vector_service
        self.minhash_service = minhash_service or MinHashPlagiarismService()

    # ---------- Public entry points ----------

    def run(
        self,
        document: "DocumentContext",
        similarity_threshold: float,
        top_k: int,
        warnings: list[str],
    ) -> PlagiarismOutcome:
        logger.info(
            "PlagiarismPipeline: scenario_id=%s chunks=%s threshold=%s top_k=%s",
            document.scenario_id,
            len(document.chunks),
            similarity_threshold,
            top_k,
        )

        local_result = self.local_similarity_service.analyze(
            scenario_id=document.scenario_id,
            current_file_path=document.file_path,
            current_text=document.cleaned_text,
            current_chunks=document.chunks,
            file_hash=document.file_hash,
            text_hash=document.text_hash,
            original_filename=document.original_filename,
        )
        excluded_scenario_ids = self._collect_same_hash_scenarios(
            file_hash=document.file_hash,
            text_hash=document.text_hash,
            current_scenario_id=document.scenario_id,
            duplicate_analyses=local_result.get("duplicate_analyses"),
        )
        vector_result, vector_available = self._analyze_vector_plagiarism(
            scenario_id=document.scenario_id,
            chunks=document.chunks,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            warnings=warnings,
            excluded_scenario_ids=excluded_scenario_ids,
            chunk_metadata=document.chunk_metadata,
        )
        # MinHash lexical fingerprint pass. Runs in parallel with the
        # embedding pipeline; the result is exposed alongside the
        # existing scores so the UI can show "plagiat textuel" next to
        # "similarité sémantique" before we make the textual signal the
        # primary verdict.
        minhash_result = self._safe_minhash_analyze(
            scenario_id=document.scenario_id,
            chunks=document.chunks,
            chunk_metadata=document.chunk_metadata,
            excluded_scenario_ids=excluded_scenario_ids,
        )
        plagiarism_result = self._merge_plagiarism_results(
            scenario_id=document.scenario_id,
            local_result=local_result,
            vector_result=vector_result,
            minhash_result=minhash_result,
        )
        strict_match = self._compute_strict_match(
            scenario_id=document.scenario_id,
            file_hash=document.file_hash,
            text_hash=document.text_hash,
            cleaned_text=document.cleaned_text,
            warnings=warnings,
        )
        # The strict-similarity verdict and the plagiarism exact-duplicate
        # signal must stay in sync: when the plagiarism stage detected an
        # exact duplicate (file_hash or text_hash match) but the strict
        # verdict still reads "different" — typically because the legacy
        # history doc doesn't expose its hashes at the top level — we
        # override the verdict so the report header doesn't contradict
        # the plagiarism section.
        strict_match = self._reconcile_strict_with_duplicate(
            strict_match=strict_match,
            plagiarism_result=plagiarism_result,
        )
        return PlagiarismOutcome(
            plagiarism_result=plagiarism_result,
            strict_match=strict_match,
            vector_available=vector_available,
        )

    def store_vectors(
        self,
        document: "DocumentContext",
        vector_available: bool,
        warnings: list[str],
    ) -> None:
        """Persist the document's chunks in Qdrant for future analyses."""
        if not vector_available:
            logger.warning(
                "Skipping vector storage because Qdrant is unavailable. "
                "scenario_id=%s",
                document.scenario_id,
            )
            return
        try:
            embeddings = self.embedding_service.generate_embeddings(document.chunks)
            stored_filename = Path(document.file_path).name if document.file_path else None
            self.vector_service.upsert_chunks(
                scenario_id=document.scenario_id,
                chunks=document.chunks,
                embeddings=embeddings,
                display_chunks=document.display_chunks,
                chunk_metadata=document.chunk_metadata,
                original_filename=document.original_filename,
                stored_filename=stored_filename,
            )
        except Exception as exc:
            error_message = _root_error_message(exc)
            logger.exception(
                "Vector storage failed for scenario_id=%s: %s",
                document.scenario_id,
                error_message,
            )
            warnings.append(
                f"Sauvegarde vectorielle indisponible: {error_message}"
            )

    # ---------- Merge / aggregate ----------

    def _safe_minhash_analyze(
        self,
        *,
        scenario_id: str,
        chunks: list[str],
        chunk_metadata: list[dict[str, Any]] | None,
        excluded_scenario_ids: set[str] | None,
    ) -> dict[str, Any]:
        """Run the MinHash pass without ever failing the main pipeline."""
        try:
            return self.minhash_service.analyze_chunks(
                scenario_id=scenario_id,
                chunks=chunks,
                chunk_metadata=chunk_metadata,
                excluded_scenario_ids=excluded_scenario_ids,
            )
        except Exception:
            logger.exception("MinHash plagiarism pass failed.")
            return {
                "scenario_id": scenario_id,
                "global_similarity_score": 0.0,
                "plagiarism_detected": False,
                "matches": [],
                "engine": "minhash",
                "error": True,
            }

    def _merge_plagiarism_results(
        self,
        scenario_id: str,
        local_result: dict[str, Any],
        vector_result: dict[str, Any],
        minhash_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Combine local + vector matches, group by source, dedupe, truncate."""
        local_score = float(local_result.get("score", 0.0) or 0.0)
        vector_score = float(
            vector_result.get("global_similarity_score", 0.0) or 0.0
        )
        local_exact_duplicate = bool(
            local_result.get("exact_duplicate")
            or local_result.get("duplicate")
            or local_result.get("duplicate_analyses")
        )
        if local_exact_duplicate:
            local_score = max(local_score, 1.0)
        final_score = max(local_score, vector_score)
        duplicate_analyses = self._dedupe_duplicate_analyses(
            local_result.get("duplicate_analyses") or []
        )
        duplicate_scenario_ids = {
            str(item.get("scenario_id"))
            for item in duplicate_analyses
            if item.get("scenario_id")
        }

        # Phase 2 — MinHash matches now feed the same merge as the
        # local / vector matches. Because their ``final_score`` is
        # computed from Jaccard (a real textual reuse signal), they
        # dominate the per-current-chunk dedupe and become the source of
        # the headline plagiarism verdict.
        raw_matches: list[dict[str, Any]] = []
        candidate_results = (local_result, minhash_result, vector_result)
        for result in candidate_results:
            if not isinstance(result, dict):
                continue
            result_matches = result.get("matches", [])
            if isinstance(result_matches, list):
                raw_matches.extend(
                    m
                    for m in result_matches
                    if isinstance(m, dict)
                    and not self._is_exact_duplicate_match(
                        m,
                        duplicate_scenario_ids=duplicate_scenario_ids,
                    )
                )

        # Phase 2 (suite) — Quand MinHash a tourné sans rien trouver,
        # on supprime tous les matches sémantiques restants : ils ne
        # reposent que sur une proximité de style (format de scénario,
        # langue partagée, registre émotionnel). Les afficher avec un
        # "35 % MODÉRÉ" donne une impression de plagiat alors que le
        # verdict global est "pas un plagiat" — c'est trompeur et ça
        # casse la crédibilité du rapport.
        #
        # On préserve les matches issus du moteur MinHash lui-même
        # (engine == "minhash") et les matches de doublon local
        # (qui ont déjà une preuve hash-level).
        minhash_ran = isinstance(minhash_result, dict) and not minhash_result.get(
            "error"
        )
        if minhash_ran and not local_exact_duplicate:
            minhash_keys: set[tuple[Any, Any]] = set()
            for m in minhash_result.get("matches", []) or []:
                if not isinstance(m, dict):
                    continue
                key = (
                    m.get("current_chunk_id"),
                    m.get("matched_scenario_id"),
                )
                minhash_keys.add(key)
            filtered: list[dict[str, Any]] = []
            for m in raw_matches:
                engine = m.get("engine") or m.get("match_type")
                # Toujours garder les matches MinHash et les matches de
                # doublon local (déjà confirmés textuellement).
                if engine in ("minhash", "exact_duplicate", "local"):
                    filtered.append(m)
                    continue
                # Pour les matches sémantiques (e5) : on ne les garde
                # que si MinHash a confirmé pour le même (chunk, source).
                key = (m.get("current_chunk_id"), m.get("matched_scenario_id"))
                if key in minhash_keys:
                    filtered.append(m)
            dropped = len(raw_matches) - len(filtered)
            if dropped > 0:
                logger.info(
                    "Dropped %s semantic-only match(es) — MinHash found no "
                    "textual reuse on those chunks.",
                    dropped,
                )
            raw_matches = filtered

        raw_matches = [self._ensure_match_quality(match) for match in raw_matches]
        aggregation = self._aggregate_matches(raw_matches)

        # Phase 2 — Le score principal est tiré de MinHash quand le
        # signal existe. MinHash mesure la reprise textuelle réelle, ce
        # qui est exactement ce qu'on veut afficher comme "score de
        # plagiat". Le composite (sémantique) ne sert plus que de
        # filet de sécurité quand MinHash est silencieux (paraphrase
        # pure, embeddings très divergents…).
        minhash_best = 0.0
        if isinstance(minhash_result, dict):
            minhash_best = float(
                minhash_result.get("global_similarity_score", 0.0) or 0.0
            )
            for m in minhash_result.get("matches", []) or []:
                if isinstance(m, dict):
                    minhash_best = max(
                        minhash_best,
                        float(m.get("minhash_score") or m.get("score") or 0.0),
                    )

        best_composite = max(
            (self._match_score(m) for m in aggregation["matches"]),
            default=0.0,
        )
        if local_exact_duplicate:
            final_score = 1.0
        elif minhash_best > 0:
            # MinHash drives the headline score. We still let the
            # composite nudge it up by a small margin if the textual
            # signal under-shot but the merged display score is higher.
            final_score = max(minhash_best, 0.7 * best_composite)
        else:
            final_score = max(best_composite, local_score)

        # Risk bucket : driven by MinHash directly when available
        # (Jaccard thresholds are calibrated for textual reuse).
        if local_exact_duplicate:
            risk = "very_high"
        elif minhash_best > 0:
            risk = self._risk_from_minhash(minhash_best)
        else:
            risk = self._risk_from_score(final_score)
            # Floor : no high/very_high without real lexical evidence.
            has_real_overlap = any(
                self._match_lexical(m) >= 0.20
                or self._match_exact_overlap(m) >= 0.10
                for m in aggregation["matches"]
            )
            if not has_real_overlap and risk in ("high", "very_high"):
                risk = "medium"

        logger.info(
            "Final similarity for scenario_id=%s: local=%s vector=%s final=%s "
            "total_matches=%s displayed=%s sources=%s/%s truncated=%s",
            scenario_id,
            local_score,
            vector_score,
            final_score,
            aggregation["total_matches"],
            aggregation["displayed_matches"],
            aggregation["displayed_sources"],
            aggregation["total_sources"],
            aggregation["is_truncated"],
        )

        result = {
            "scenario_id": scenario_id,
            "score": round(final_score, 4),
            "raw_score": round(final_score, 4),
            # Public percentages are always integers (0..100). Decimals
            # routinely confused users (44.51% / 85.07%) for a value that
            # represents an estimate, not a measurement.
            "score_percent": format_percent(final_score),
            "similarity": format_percent(final_score),
            "display_score": format_percent(final_score),
            "risk": risk,
            "duplicate": local_exact_duplicate,
            "exact_duplicate": bool(duplicate_analyses),
            "duplicate_count": len(duplicate_analyses),
            "duplicate_analyses": duplicate_analyses,
            "global_similarity_score": round(final_score, 4),
            # Phase 2 — la décision "plagiat détecté" suit MinHash en
            # priorité : un Jaccard >= 10 % est un signal textuel fiable.
            # Sans MinHash, on retombe sur la règle composite historique.
            "plagiarism_detected": (
                local_exact_duplicate
                or minhash_best >= 0.10
                or (minhash_best == 0.0 and final_score >= 0.4)
            ),
            "matches": aggregation["matches"],
            "sources": aggregation["plagiarism_sources"],
            "plagiarism_sources": aggregation["plagiarism_sources"],
            "total_matches": aggregation["total_matches"],
            "displayed_matches": aggregation["displayed_matches"],
            "total_sources": aggregation["total_sources"],
            "displayed_sources": aggregation["displayed_sources"],
            "is_truncated": aggregation["is_truncated"],
            # ``local`` and ``vector`` keep only headline metadata so the
            # final dict stays under the 16 MiB BSON limit even when the
            # vector search returned thousands of raw matches (full match
            # lists have already been merged into ``matches`` /
            # ``plagiarism_sources`` above).
            "local": self._slim_subresult(local_result),
            "vector": self._slim_subresult(vector_result),
            # Phase 1 — MinHash runs alongside the embedding pipeline and
            # its score is surfaced in the report so we can compare both
            # verdicts on real documents. The main verdict (above) still
            # comes from the existing semantic / composite scoring.
            "minhash": self._slim_minhash_subresult(minhash_result),
        }
        if settings.PLAGIARISM_DIAGNOSTICS_ENABLED:
            result["diagnostics"] = self._build_plagiarism_diagnostics(
                local_result=local_result,
                vector_result=vector_result,
                aggregation=aggregation,
            )
        return result

    @staticmethod
    def _slim_minhash_subresult(
        result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Compact MinHash result shape for the analysis document."""
        if not isinstance(result, dict):
            return {
                "engine": "minhash",
                "global_similarity_score": 0.0,
                "score_percent": 0,
                "matches_count": 0,
                "plagiarism_detected": False,
            }
        score = float(result.get("global_similarity_score", 0.0) or 0.0)
        matches = result.get("matches") or []
        # Best per-source aggregation: max jaccard per matched scenario.
        per_source: dict[str, float] = {}
        for m in matches:
            if not isinstance(m, dict):
                continue
            sid = str(m.get("matched_scenario_id") or "")
            if not sid:
                continue
            s = float(m.get("minhash_score") or m.get("score") or 0.0)
            if s > per_source.get(sid, 0.0):
                per_source[sid] = s
        best = max(per_source.values(), default=score)
        return {
            "engine": "minhash",
            "global_similarity_score": round(score, 4),
            "best_source_score": round(best, 4),
            "score_percent": format_percent(best),
            "matches_count": len(matches),
            "sources_count": len(per_source),
            "plagiarism_detected": bool(result.get("plagiarism_detected")),
        }

    @staticmethod
    def _slim_subresult(result: dict[str, Any]) -> dict[str, Any]:
        """Return a small metadata-only copy of a sub-pipeline result.

        The full match lists routinely exceed several MB on long PDFs
        (96 chunks × top_k 15 = thousands of raw Qdrant hits). Storing
        them twice — once aggregated in ``matches`` and once raw under
        ``local`` / ``vector`` — pushes MongoDB documents past the 16 MiB
        BSON limit. We keep counts and status fields, drop the heavy
        arrays.
        """
        if not isinstance(result, dict):
            return {}
        keep_keys = {
            "score",
            "global_similarity_score",
            "risk",
            "duplicate",
            "exact_duplicate",
            "duplicate_count",
            "plagiarism_detected",
            "status",
            "error",
        }
        slim: dict[str, Any] = {k: result[k] for k in keep_keys if k in result}
        matches = result.get("matches")
        if isinstance(matches, list):
            slim["matches_count"] = len(matches)
        diagnostics = result.get("diagnostics")
        if isinstance(diagnostics, dict):
            slim["diagnostics"] = diagnostics
        return slim

    @classmethod
    def _is_keepable_match(cls, match: dict[str, Any]) -> bool:
        """Composite-aware filter applied before deduplication.

        A match is kept only if it carries enough plagiarism evidence:
          * composite ``final_score`` >= 0.30, OR
          * ``exact_overlap_score`` >= 0.15 (real shared n-gram), OR
          * the match is an explicit exact duplicate.
        Legacy matches without composite metadata pass through unchanged so
        the local-history pipeline keeps working.
        """
        if match.get("match_type") == "exact_duplicate":
            return True
        if match.get("duplicate") is True:
            return True
        if "final_score" not in match and "semantic_score" not in match:
            return True  # legacy match — let downstream logic decide
        final = cls._match_score(match)
        if final >= 0.30:
            return True
        if cls._match_exact_overlap(match) >= 0.15:
            return True
        return False

    def _aggregate_matches(
        self, raw_matches: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Dedupe matches, group by source, then truncate for display."""
        total_matches_raw = len(raw_matches)
        raw_matches = [m for m in raw_matches if self._is_keepable_match(m)]

        deduped_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
        duplicate_counts: dict[tuple[Any, ...], int] = {}
        for match in raw_matches:
            key = self._match_dedupe_key(match)
            duplicate_counts[key] = duplicate_counts.get(key, 0) + 1
            existing = deduped_by_key.get(key)
            if existing is None or self._is_better_duplicate(match, existing):
                deduped_by_key[key] = match

        for key, match in deduped_by_key.items():
            grouped = duplicate_counts[key]
            if grouped > 1:
                match["grouped_copies"] = grouped

        deduped_matches = list(deduped_by_key.values())

        groups: dict[str, dict[str, Any]] = {}
        for match in deduped_matches:
            source_key = self._source_key(match)
            group = groups.setdefault(
                source_key,
                {
                    "source_scenario_id": match.get("matched_scenario_id"),
                    "original_filename": match.get("original_filename"),
                    "stored_filename": (
                        match.get("stored_filename")
                        or match.get("filename")
                        or match.get("matched_chunk_id")
                    ),
                    "best_score": 0.0,
                    "matches_count": 0,
                    "matches": [],
                },
            )
            score = self._match_score(match)
            if score > group["best_score"]:
                group["best_score"] = score
                if match.get("original_filename"):
                    group["original_filename"] = match["original_filename"]
                if match.get("stored_filename"):
                    group["stored_filename"] = match["stored_filename"]
                if match.get("matched_scenario_id"):
                    group["source_scenario_id"] = match["matched_scenario_id"]
            group["matches_count"] += 1
            group["matches"].append(match)

        for group in groups.values():
            # Per-source dedup: a single user chunk should appear at most
            # once per source document — keep its highest-scoring match
            # against that source. Without this, one chunk that hits two
            # nearby source chunks inflates the report with near-duplicate
            # rows (e.g. user page 2 ↔ source page 2 AND source page 4).
            best_per_current: dict[Any, dict[str, Any]] = {}
            for match in group["matches"]:
                current_key = (
                    match.get("current_chunk_id")
                    or match.get("current_chunk_index")
                    or match.get("chunk_index")
                    or id(match)
                )
                existing = best_per_current.get(current_key)
                if existing is None or self._match_score(match) > self._match_score(existing):
                    best_per_current[current_key] = match
            group["matches"] = list(best_per_current.values())
            group["matches_count"] = len(group["matches"])
            group["matches"].sort(
                key=cmp_to_key(self._compare_matches_for_display)
            )
            # Expose the composite best as both raw float (compat) and
            # integer percentages so the UI renders 0–100 without decimals.
            group["best_score_raw"] = round(float(group["best_score"]), 4)
            group["best_score_percent"] = format_percent(group["best_score"])
            group["display_score"] = group["best_score_percent"]

        sorted_sources = sorted(
            groups.values(),
            key=lambda g: (
                g["best_score"],
                max((self._match_quality(m) for m in g["matches"]), default=0.0),
            ),
            reverse=True,
        )
        total_sources = len(sorted_sources)
        truncated_sources = sorted_sources[: self.MAX_SOURCES_DISPLAYED]

        displayed_matches_flat: list[dict[str, Any]] = []
        displayed_groups: list[dict[str, Any]] = []
        for group in truncated_sources:
            full_count = group["matches_count"]
            kept = group["matches"][: self.MAX_MATCHES_PER_SOURCE]
            group_copy = {
                **group,
                "matches": kept,
                "matches_count": full_count,
                "displayed_matches_count": len(kept),
            }
            displayed_groups.append(group_copy)
            displayed_matches_flat.extend(kept)

        displayed_matches_flat = sorted(
            displayed_matches_flat,
            key=cmp_to_key(self._compare_matches_for_display),
        )[: self.MAX_TOTAL_MATCHES_DISPLAYED]

        # ``total_after_dedupe`` must reflect the *final* match count after
        # both the global dedupe and the per-source/per-current-chunk dedupe.
        # Otherwise the UI's "X affichés sur Y détectés" banner fires even
        # when the only delta is internal deduplication, not display capping.
        total_after_dedupe = sum(
            len(group["matches"]) for group in groups.values()
        )
        is_truncated = (
            total_after_dedupe > len(displayed_matches_flat)
            or total_sources > len(displayed_groups)
        )

        return {
            "matches": displayed_matches_flat,
            "plagiarism_sources": displayed_groups,
            "total_matches": total_after_dedupe,
            "displayed_matches": len(displayed_matches_flat),
            "total_sources": total_sources,
            "displayed_sources": len(displayed_groups),
            "is_truncated": is_truncated,
            "raw_matches_count": total_matches_raw,
        }

    def _build_plagiarism_diagnostics(
        self,
        local_result: dict[str, Any],
        vector_result: dict[str, Any],
        aggregation: dict[str, Any],
    ) -> dict[str, Any]:
        vector_diagnostics = vector_result.get("diagnostics")
        if not isinstance(vector_diagnostics, dict):
            vector_diagnostics = {}
        local_matches = local_result.get("matches")
        vector_matches = vector_result.get("matches")
        local_count = len(local_matches) if isinstance(local_matches, list) else 0
        vector_count = (
            len(vector_matches) if isinstance(vector_matches, list) else 0
        )
        raw_count = int(aggregation.get("raw_matches_count") or 0)
        deduped_count = int(aggregation.get("total_matches") or 0)

        return {
            "chunks_generated": int(vector_diagnostics.get("chunks_analyzed") or 0),
            "raw_qdrant_matches": int(
                vector_diagnostics.get("raw_qdrant_results_count") or vector_count
            ),
            "raw_local_matches": local_count,
            "raw_matches_before_deduplication": raw_count,
            "matches_after_deduplication": deduped_count,
            "displayed_matches": int(aggregation.get("displayed_matches") or 0),
            "deduped_matches": max(0, raw_count - deduped_count),
            "filtered_matches": 0,
            "filter_reasons": [],
        }

    # ---------- Match-quality helpers ----------

    @staticmethod
    def _match_score(match: dict[str, Any]) -> float:
        """Composite plagiarism score used for ranking and risk decisions.

        Falls back to the raw semantic score only when no composite has been
        attached (e.g. matches coming from the legacy local pipeline).
        """
        for key in ("final_score", "similarity_score", "similarity", "score"):
            value = match.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _match_semantic_score(match: dict[str, Any]) -> float:
        for key in ("semantic_score", "similarity_score", "similarity", "score"):
            value = match.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _match_exact_overlap(match: dict[str, Any]) -> float:
        try:
            return float(match.get("exact_overlap_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _match_lexical(match: dict[str, Any]) -> float:
        try:
            return float(match.get("lexical_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _match_quality(match: dict[str, Any]) -> float:
        try:
            return float(match.get("match_quality_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _informative_word_count(match: dict[str, Any]) -> int:
        try:
            return int(match.get("informative_word_count") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _source_key(match: dict[str, Any]) -> str:
        """Stable group key for matches sharing the same source document."""
        for field in (
            "source_file_hash",
            "source_text_hash",
            "matched_scenario_id",
            "stored_filename",
            "filename",
            "original_filename",
            "matched_chunk_id",
        ):
            value = match.get(field)
            if value:
                return f"{field}:{value}"
        return "source:unknown"

    @staticmethod
    def _snippet_signature(match: dict[str, Any]) -> str:
        """Normalized signature to dedupe near-identical extracts."""
        text = (
            match.get("matched_chunk_text_display")
            or match.get("matched_chunk_text")
            or match.get("matched_text")
            or ""
        )
        if not isinstance(text, str):
            text = str(text)
        compact = re.sub(r"\s+", " ", text).strip().lower()
        return compact[:200]

    @staticmethod
    def _is_exact_duplicate_match(
        match: dict[str, Any],
        duplicate_scenario_ids: set[str],
    ) -> bool:
        if (
            match.get("match_type") == "exact_duplicate"
            or match.get("duplicate") is True
        ):
            return True
        matched_scenario_id = match.get("matched_scenario_id")
        return bool(
            matched_scenario_id
            and str(matched_scenario_id) in duplicate_scenario_ids
        )

    @staticmethod
    def _dedupe_duplicate_analyses(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        deduped: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("scenario_id"),
                item.get("stored_filename"),
                item.get("file_hash"),
                item.get("text_hash"),
            )
            if key in deduped:
                existing = deduped[key]
                for field, value in item.items():
                    if existing.get(field) in (None, "") and value not in (None, ""):
                        existing[field] = value
                continue
            deduped[key] = dict(item)
        return list(deduped.values())

    @staticmethod
    def _position_for_sort(match: dict[str, Any]) -> int:
        value = (
            match.get("current_chunk_index")
            if match.get("current_chunk_index") is not None
            else match.get("chunk_index")
        )
        if value is None:
            value = (
                match.get("current_page_number")
                if match.get("current_page_number") is not None
                else match.get("page_number")
            )
        if isinstance(value, int):
            return value
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 1_000_000

    def _is_better_duplicate(
        self,
        candidate: dict[str, Any],
        existing: dict[str, Any],
    ) -> bool:
        score_delta = self._match_score(candidate) - self._match_score(existing)
        if abs(score_delta) >= 0.03:
            return score_delta > 0
        quality_delta = self._match_quality(candidate) - self._match_quality(existing)
        if abs(quality_delta) > 0.0001:
            return quality_delta > 0
        if abs(score_delta) > 0.0001:
            return score_delta > 0
        return self._safe_float(
            candidate.get("boilerplate_ratio")
        ) < self._safe_float(existing.get("boilerplate_ratio"))

    def _decorate_match_display(self, match: dict[str, Any]) -> dict[str, Any]:
        """Ensure every match carries integer display fields for the UI."""
        final = self._match_score(match)
        match["final_score"] = round(float(match.get("final_score", final) or final), 4)
        match["display_score"] = format_percent(final)
        match["score_percent"] = format_percent(final)
        return match

    def _ensure_match_quality(self, match: dict[str, Any]) -> dict[str, Any]:
        text = self._best_snippet_text(match)
        words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        informative = [
            word
            for word in words
            if len(word) > 2 and word not in self.LOW_INFORMATION_WORDS
        ]
        unique_informative = set(informative)
        boilerplate_ratio = self._safe_float(match.get("boilerplate_ratio"))
        if match.get("boilerplate_ratio") is None:
            boilerplate_ratio = self._estimate_boilerplate_ratio(text)
        length_factor = min(1.0, len(informative) / 45.0)
        unique_factor = min(1.0, len(unique_informative) / 28.0)
        sentence_factor = min(1.0, self._best_sentence_quality(text) / 20.0)
        long_phrase_bonus = (
            0.1 if self._has_long_informative_phrase(text) else 0.0
        )
        lexical_quality = (
            0.4 * length_factor
            + 0.3 * unique_factor
            + 0.2 * sentence_factor
            + long_phrase_bonus
        )
        quality = max(0.0, min(1.0, lexical_quality * (1.0 - boilerplate_ratio)))
        updated = dict(match)
        updated["snippet"] = self._preview_snippet(text)
        updated["boilerplate_ratio"] = round(boilerplate_ratio, 4)
        updated["informative_word_count"] = len(informative)
        updated["match_quality_score"] = round(quality, 4)
        return self._decorate_match_display(updated)

    def _compare_matches_for_display(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> int:
        left_score = self._match_score(left)
        right_score = self._match_score(right)
        score_delta = left_score - right_score
        if abs(score_delta) >= 0.03:
            return -1 if score_delta > 0 else 1

        left_quality = self._match_quality(left)
        right_quality = self._match_quality(right)
        quality_delta = left_quality - right_quality
        if abs(quality_delta) > 0.0001:
            return -1 if quality_delta > 0 else 1

        if abs(score_delta) > 0.0001:
            return -1 if score_delta > 0 else 1

        boilerplate_delta = (
            self._safe_float(left.get("boilerplate_ratio"))
            - self._safe_float(right.get("boilerplate_ratio"))
        )
        if abs(boilerplate_delta) > 0.0001:
            return -1 if boilerplate_delta < 0 else 1

        position_delta = (
            self._position_for_sort(left) - self._position_for_sort(right)
        )
        if position_delta:
            return -1 if position_delta < 0 else 1
        return 0

    def _best_snippet_text(self, match: dict[str, Any]) -> str:
        for field in self.OVERLAP_TEXT_FIELDS:
            value = match.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()

        source = (
            match.get("snippet")
            or match.get("matched_chunk_text_display")
            or match.get("matched_chunk_text")
            or match.get("matched_text_display")
            or match.get("matched_text")
            or match.get("similar_text")
            or ""
        )
        if not isinstance(source, str):
            source = str(source or "")
        source = source.strip()
        if not source:
            return ""

        sentences = self._split_candidate_sentences(source)
        if not sentences:
            return source
        return max(sentences, key=self._sentence_informativeness)

    def _split_candidate_sentences(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []
        parts = re.split(r"(?<=[.!?;:])\s+|\s+-\s+|\n+", normalized)
        return [part.strip() for part in parts if part and part.strip()]

    def _sentence_informativeness(self, sentence: str) -> float:
        words = re.findall(r"\w+", sentence.lower(), flags=re.UNICODE)
        if not words:
            return 0.0
        informative = [
            word
            for word in words
            if len(word) > 2 and word not in self.LOW_INFORMATION_WORDS
        ]
        unique_ratio = len(set(words)) / max(len(words), 1)
        generic_penalty = self._estimate_boilerplate_ratio(sentence)
        long_sentence_bonus = 2.0 if len(words) >= 12 else 0.0
        return (
            len(informative)
            + len(set(informative)) * 0.5
            + unique_ratio * 3.0
            + long_sentence_bonus
            - generic_penalty * 8.0
        )

    def _best_sentence_quality(self, text: str) -> float:
        sentences = self._split_candidate_sentences(text)
        if not sentences:
            return 0.0
        return max(
            self._sentence_informativeness(sentence) for sentence in sentences
        )

    def _has_long_informative_phrase(self, text: str) -> bool:
        return any(
            len(re.findall(r"\w+", sentence, flags=re.UNICODE)) >= 12
            and self._sentence_informativeness(sentence) >= 10
            for sentence in self._split_candidate_sentences(text)
        )

    def _estimate_boilerplate_ratio(self, text: str) -> float:
        if not isinstance(text, str) or not text.strip():
            return 0.0
        lines = [
            line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()
        ]
        if not lines:
            lines = [text.strip()]
        normalized_lines = [re.sub(r"\s+", " ", line).lower() for line in lines]
        repeated = {
            line for line in normalized_lines if normalized_lines.count(line) > 1
        }
        boilerplate = 0
        for line, normalized in zip(lines, normalized_lines, strict=False):
            words = re.findall(r"\w+", normalized, flags=re.UNICODE)
            if normalized in repeated:
                boilerplate += 1
            elif re.fullmatch(r"(page\s*)?\d+(/\d+)?", normalized):
                boilerplate += 1
            elif len(words) <= 5 and any(
                word in self.LOW_INFORMATION_WORDS for word in words
            ):
                boilerplate += 1
        return round(boilerplate / max(len(lines), 1), 4)

    @staticmethod
    def _preview_snippet(text: str, limit: int = 800) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[:limit].rstrip()

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _match_dedupe_key(self, match: dict[str, Any]) -> tuple[Any, ...]:
        """Only collapse matches that are the same passage, not same source."""
        current_page = match.get("current_page_number") or match.get("page_number")
        source_page = match.get("source_page_number")
        snippet = self._snippet_signature(match)
        if snippet and current_page is None and source_page is None:
            return (self._source_key(match), snippet)
        return (
            self._source_key(match),
            match.get("source_chunk_id") or match.get("matched_chunk_id"),
            match.get("current_chunk_id"),
            current_page,
            source_page,
            snippet,
        )

    @staticmethod
    def _risk_from_score(score: float) -> str:
        """Risk bucket derived from the composite ``final_score``.

        New thresholds (per the stricter spec):
            < 0.30 → low, < 0.55 → medium, < 0.75 → high, else very_high.
        """
        if score >= 0.75:
            return "very_high"
        if score >= 0.55:
            return "high"
        if score >= 0.30:
            return "medium"
        return "low"

    @staticmethod
    def _risk_from_minhash(jaccard: float) -> str:
        """Risk bucket derived from MinHash Jaccard.

        MinHash thresholds are tighter than embedding-derived ones
        because Jaccard on token shingles is a direct measurement of
        textual reuse — a value above 0.25 already means a quarter of
        the informative shingles are shared.
        """
        if jaccard >= 0.40:
            return "very_high"
        if jaccard >= 0.20:
            return "high"
        if jaccard >= 0.10:
            return "medium"
        return "low"

    # ---------- Vector + strict similarity ----------

    def _collect_same_hash_scenarios(
        self,
        file_hash: str,
        text_hash: str,
        current_scenario_id: str,
        duplicate_analyses: Any = None,
    ) -> set[str]:
        """Return scenario_ids of previous analyses sharing exact hashes."""
        excluded: set[str] = {
            str(item.get("scenario_id"))
            for item in self._dedupe_duplicate_analyses(duplicate_analyses)
            if item.get("scenario_id")
        }
        if not file_hash and not text_hash:
            return excluded
        try:
            repository = self.local_similarity_service.analysis_repository
            finder = getattr(repository, "find_exact_duplicates", None)
            if callable(finder):
                documents = finder(
                    file_hash=file_hash,
                    text_hash=text_hash,
                    exclude_scenario_id=current_scenario_id,
                    limit=20,
                )
            elif file_hash:
                documents = repository.find_by_file_hash(
                    file_hash=file_hash,
                    exclude_scenario_id=current_scenario_id,
                    limit=20,
                )
            else:
                documents = []
        except Exception as exc:
            logger.debug(
                "MongoDB lookup for same-hash scenarios failed: %s",
                exc,
                exc_info=True,
            )
            return excluded
        for doc in documents:
            sid = doc.get("scenario_id")
            if sid:
                excluded.add(str(sid))
        if excluded:
            logger.info(
                "Excluding %s scenario(s) with same file_hash from Qdrant matches.",
                len(excluded),
            )
        return excluded

    def _analyze_vector_plagiarism(
        self,
        scenario_id: str,
        chunks: list[str],
        similarity_threshold: float,
        top_k: int,
        warnings: list[str],
        excluded_scenario_ids: set[str] | None = None,
        chunk_metadata: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Run plagiarism detection, returning an unavailable result if Qdrant fails."""
        try:
            plagiarism_result = self.plagiarism_service.analyze_chunks(
                scenario_id=scenario_id,
                chunks=chunks,
                similarity_threshold=similarity_threshold,
                top_k=top_k,
                excluded_scenario_ids=excluded_scenario_ids,
                chunk_metadata=chunk_metadata,
            )
            return plagiarism_result, True
        except Exception as exc:
            error_message = _root_error_message(exc)
            logger.exception(
                "Plagiarism analysis unavailable for scenario_id=%s: %s",
                scenario_id,
                error_message,
            )
            warnings.append(f"Analyse plagiat indisponible: {error_message}")
            return (
                {
                    "scenario_id": scenario_id,
                    "global_similarity_score": 0.0,
                    "plagiarism_detected": False,
                    "matches": [],
                    "status": "unavailable",
                    "error": error_message,
                },
                False,
            )

    @staticmethod
    def _reconcile_strict_with_duplicate(
        *,
        strict_match: dict[str, Any],
        plagiarism_result: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(strict_match, dict):
            return strict_match
        if not bool(plagiarism_result.get("exact_duplicate")):
            return strict_match
        if str(strict_match.get("verdict")) != "different":
            return strict_match

        duplicate_analyses = plagiarism_result.get("duplicate_analyses") or []
        first = duplicate_analyses[0] if duplicate_analyses else {}
        matched = None
        if isinstance(first, dict) and first:
            matched = {
                "scenario_id": str(first.get("scenario_id") or ""),
                "original_filename": first.get("original_filename"),
                "stored_filename": first.get("stored_filename")
                or first.get("filename"),
                "analyzed_at": first.get("analyzed_at")
                or first.get("analysis_timestamp"),
                "risk_level": first.get("risk_level"),
                "file_hash": first.get("file_hash"),
                "text_hash": first.get("text_hash"),
                "similarity_score": 1.0,
                "score_percent": 100.0,
                "match_type": "exact_duplicate",
            }
        count_text = (
            f"{len(duplicate_analyses)} analyse(s) antérieure(s)"
            if duplicate_analyses
            else "une analyse antérieure"
        )
        strict_match.update(
            {
                "verdict": "identical",
                "score": 1.0,
                "score_percent": 100.0,
                "match_type": "exact_duplicate",
                "is_renewal_candidate": True,
                "reason": (
                    "Doublon exact déjà présent dans l'historique "
                    f"({count_text})."
                ),
                "matched_analysis": matched,
            }
        )
        return strict_match

    def _compute_strict_match(
        self,
        *,
        scenario_id: str,
        file_hash: str,
        text_hash: str,
        cleaned_text: str,
        warnings: list[str],
    ) -> dict[str, Any]:
        try:
            verdict = self.strict_similarity_service.compute(
                current_scenario_id=scenario_id,
                current_file_hash=file_hash,
                current_text_hash=text_hash,
                current_cleaned_text=cleaned_text,
            )
            return verdict.to_dict()
        except Exception as exc:
            logger.exception(
                "Strict similarity verdict failed for scenario_id=%s.",
                scenario_id,
            )
            warnings.append(
                f"Verdict de stricte similarité indisponible: "
                f"{_root_error_message(exc)}"
            )
            return {
                "verdict": "different",
                "score": 0.0,
                "score_percent": 0.0,
                "match_type": "none",
                "is_renewal_candidate": False,
                "reason": (
                    "Verdict indisponible (erreur interne). À traiter comme "
                    "une nouvelle demande par défaut."
                ),
                "candidates_compared": 0,
                "matched_analysis": None,
                "extras": [],
                "status": "unavailable",
            }


def _root_error_message(exc: BaseException) -> str:
    """Return the deepest useful exception message for API/debug logs."""
    current: BaseException = exc
    while current.__cause__ is not None:
        current = current.__cause__
    return str(current) or current.__class__.__name__

"""MinHash-based plagiarism analysis.

Plays the same role as ``PlagiarismService`` (Qdrant + e5) but on the
lexical fingerprinting layer. Iterates over the document's chunks,
queries the shared ``MinHashIndex``, and produces a list of matches
shaped the same way as the existing pipeline so the merge logic in
``PlagiarismPipeline`` can consume them without modification.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.minhash_service import MinHashIndex, bootstrap_from_qdrant
from backend.utils.composite_scoring import (
    extract_dialogue_lines,
    extract_named_entities,
    jaccard_score,
    ngram_overlap_score,
    normalize_tokens,
)
from backend.utils.text_overlap import build_plagiarism_snippet


logger = logging.getLogger(__name__)


class MinHashPlagiarismService:
    """Detect plagiarism by lexical fingerprinting (MinHash + LSH)."""

    # Minimum Jaccard score above which a MinHash candidate is reported.
    DEFAULT_MIN_JACCARD = 0.10

    def __init__(self, index: MinHashIndex | None = None) -> None:
        self.index = index or MinHashIndex.get()

    def _ensure_bootstrapped(self) -> None:
        """Keep this worker's MinHash index in sync with Qdrant.

        Uvicorn spawns multiple worker processes; each owns its own
        ``MinHashIndex`` singleton. A document upserted via worker A
        only lands in worker A's index — worker B would miss it until
        it scrolls Qdrant. We therefore do an *incremental* sync at the
        start of every analysis: ``add_chunk`` is a no-op for keys we
        already have, so this stays cheap once the index is warm.
        """
        try:
            from backend.services.vector_service import VectorService

            # Reset the bootstrapped flag so ``bootstrap_from_qdrant``
            # actually scrolls Qdrant. The function itself is idempotent
            # — ``add_chunk`` skips keys already indexed — so calling it
            # every time only costs one Qdrant scroll.
            self.index._bootstrapped = False  # type: ignore[attr-defined]
            count = bootstrap_from_qdrant(VectorService())
            logger.info(
                "MinHash incremental sync: %s chunks now indexed.", count
            )
        except Exception:
            logger.exception("MinHash incremental sync failed.")

    def analyze_chunks(
        self,
        scenario_id: str,
        chunks: list[str],
        chunk_metadata: list[dict[str, Any]] | None = None,
        excluded_scenario_ids: set[str] | None = None,
        min_jaccard: float = DEFAULT_MIN_JACCARD,
        per_chunk_limit: int = 5,
    ) -> dict[str, Any]:
        """Return matches keyed by chunk index.

        The output shape mirrors what ``PlagiarismService.analyze_chunks``
        produces so ``PlagiarismPipeline`` can merge both result sets
        without per-source-specific logic.
        """
        if not chunks:
            return {
                "scenario_id": scenario_id,
                "global_similarity_score": 0.0,
                "plagiarism_detected": False,
                "matches": [],
            }

        self._ensure_bootstrapped()

        metadata_by_index = (
            chunk_metadata
            if isinstance(chunk_metadata, list)
            and len(chunk_metadata) == len(chunks)
            else [{} for _ in chunks]
        )

        matches: list[dict[str, Any]] = []
        best_scores_by_chunk: list[float] = []

        for chunk_index, chunk_text in enumerate(chunks):
            candidates = self.index.search(
                text=chunk_text,
                exclude_scenario_id=scenario_id,
                excluded_scenario_ids=excluded_scenario_ids,
                limit=per_chunk_limit,
            )
            kept_for_chunk = []
            for cand in candidates:
                jaccard = float(cand.get("score", 0.0))
                if jaccard < min_jaccard:
                    continue
                payload = cand.get("payload") or {}
                source_text = (
                    payload.get("chunk_text_display")
                    or payload.get("chunk_text")
                    or ""
                )
                match = self._build_match(
                    scenario_id=scenario_id,
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    chunk_metadata=metadata_by_index[chunk_index],
                    source_text=str(source_text),
                    payload=payload,
                    minhash_score=jaccard,
                )
                matches.append(match)
                kept_for_chunk.append(jaccard)
            best_scores_by_chunk.append(max(kept_for_chunk, default=0.0))

        global_score = (
            round(sum(best_scores_by_chunk) / len(best_scores_by_chunk), 4)
            if best_scores_by_chunk
            else 0.0
        )

        logger.info(
            "MinHash analysis: scenario=%s chunks=%s matches=%s global=%s",
            scenario_id,
            len(chunks),
            len(matches),
            global_score,
        )

        return {
            "scenario_id": scenario_id,
            "global_similarity_score": global_score,
            "plagiarism_detected": bool(matches),
            "matches": matches,
            "engine": "minhash",
        }

    def _build_match(
        self,
        *,
        scenario_id: str,
        chunk_index: int,
        chunk_text: str,
        chunk_metadata: dict[str, Any],
        source_text: str,
        payload: dict[str, Any],
        minhash_score: float,
    ) -> dict[str, Any]:
        # Reuse the same secondary signals so the merge / display layer
        # gets a coherent match shape — but the *primary* score is
        # MinHash Jaccard, which is much harder to fool than e5 cosine.
        tokens_q = normalize_tokens(chunk_text)
        tokens_s = normalize_tokens(source_text)
        lexical = jaccard_score(tokens_q, tokens_s)
        exact = ngram_overlap_score(tokens_q, tokens_s)
        entities_a = extract_named_entities(chunk_text)
        entities_b = extract_named_entities(source_text)
        entity_overlap = (
            len(entities_a & entities_b)
            / min(len(entities_a), len(entities_b))
            if entities_a and entities_b
            else 0.0
        )
        dialogue_a = extract_dialogue_lines(chunk_text)
        dialogue_b = extract_dialogue_lines(source_text)
        dialogue = (
            jaccard_score(
                normalize_tokens(" ".join(dialogue_a)),
                normalize_tokens(" ".join(dialogue_b)),
            )
            if dialogue_a and dialogue_b
            else 0.0
        )

        snippet_info = build_plagiarism_snippet(
            current_text=chunk_text,
            source_text=source_text,
            fallback_text=source_text,
            max_chars=900,
            min_chars=400,
        )

        current_chunk_id = (
            chunk_metadata.get("chunk_id") or f"{scenario_id}_{chunk_index}"
        )
        return {
            "engine": "minhash",
            "chunk_index": chunk_index,
            "current_chunk_index": chunk_metadata.get("chunk_index", chunk_index),
            "current_chunk_id": current_chunk_id,
            "chunk_text": chunk_text,
            "snippet": snippet_info["snippet"],
            "snippet_source": snippet_info["snippet_source"],
            "overlap_text": snippet_info["overlap_text"],
            "matched_scenario_id": payload.get("scenario_id"),
            "matched_chunk_id": payload.get("chunk_id"),
            "source_chunk_id": payload.get("chunk_id"),
            "source_chunk_index": payload.get("chunk_index"),
            "original_filename": payload.get("original_filename"),
            "stored_filename": payload.get("stored_filename"),
            "filename": payload.get("original_filename"),
            "matched_chunk_text": payload.get("chunk_text"),
            "matched_chunk_text_display": (
                payload.get("chunk_text_display") or payload.get("chunk_text")
            ),
            "page_number": payload.get("page_number"),
            "current_page_number": chunk_metadata.get("page_number"),
            "source_page_number": payload.get("page_number"),
            # Primary MinHash signal.
            "minhash_score": round(minhash_score, 4),
            "similarity_score": round(minhash_score, 4),
            "score": round(minhash_score, 4),
            # Secondary signals — let the report show context.
            "semantic_score": 0.0,
            "lexical_score": round(lexical, 4),
            "exact_overlap_score": round(exact, 4),
            "named_entity_overlap_score": round(entity_overlap, 4),
            "dialogue_overlap_score": round(dialogue, 4),
            # Final score for the UI: MinHash drives it, but a strong
            # named-entity / n-gram overlap bumps it slightly so a real
            # copy with the same characters reads as higher confidence.
            "final_score": round(
                min(
                    1.0,
                    0.75 * minhash_score
                    + 0.15 * exact
                    + 0.10 * entity_overlap,
                ),
                4,
            ),
            "raw_score": round(minhash_score, 4),
            "display_score": round(minhash_score * 100),
            "risk": self._risk_from_jaccard(minhash_score, exact, entity_overlap),
            "match_type": "minhash",
        }

    @staticmethod
    def _risk_from_jaccard(
        jaccard: float,
        exact: float,
        entity_overlap: float,
    ) -> str:
        # MinHash thresholds are tighter than cosine because the signal
        # is more reliable. >= 30 % shared shingles is already very
        # strong evidence of textual reuse.
        if jaccard >= 0.40 or (jaccard >= 0.25 and exact >= 0.20):
            return "very_high"
        if jaccard >= 0.20 or (jaccard >= 0.12 and entity_overlap >= 0.30):
            return "high"
        if jaccard >= 0.10:
            return "medium"
        return "low"

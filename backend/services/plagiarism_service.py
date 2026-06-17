import logging
import re
from typing import Any

from backend.core.config import settings
from backend.services.embedding_service import EmbeddingService
from backend.services.vector_service import VectorService
from backend.utils.composite_scoring import (
    compute_composite_scores,
    format_percent,
    is_likely_false_positive,
    risk_from_composite,
)
from backend.utils.text_overlap import (
    build_plagiarism_snippet,
    collect_boilerplate_ngrams,
)


logger = logging.getLogger(__name__)


class PlagiarismService:
    """Service responsible for detecting scenario similarities using Qdrant."""

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
    ) -> None:
        """Initialize the plagiarism service.

        Args:
            embedding_service: Service used to generate chunk embeddings.
            vector_service: Service used to search similar vectors in Qdrant.
        """
        self.embedding_service = embedding_service or EmbeddingService()
        self.vector_service = vector_service or VectorService()

    def analyze_chunks(
        self,
        scenario_id: str,
        chunks: list[str],
        similarity_threshold: float = settings.PLAGIARISM_SIMILARITY_THRESHOLD,
        top_k: int = settings.PLAGIARISM_TOP_K,
        excluded_scenario_ids: set[str] | None = None,
        chunk_metadata: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Analyze cleaned chunks against indexed scenarios to detect plagiarism.

        Args:
            scenario_id: Identifier of the scenario being analyzed.
            chunks: Cleaned text chunks to compare with indexed chunks.
            similarity_threshold: Minimum similarity score required to keep a match.
            top_k: Maximum number of Qdrant results to inspect for each chunk.

        Returns:
            A dictionary containing the scenario id, global similarity score,
            plagiarism flag, and suspicious matches.

        Raises:
            ValueError: If inputs are invalid.
            RuntimeError: If embedding generation or vector search fails.
        """
        self._validate_inputs(
            scenario_id=scenario_id,
            chunks=chunks,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
        )

        excluded = {str(sid) for sid in (excluded_scenario_ids or set()) if sid}

        try:
            logger.info(
                "Starting plagiarism analysis for scenario_id=%s with %s chunks "
                "(excluded scenarios: %s).",
                scenario_id,
                len(chunks),
                len(excluded),
            )
            # Chunks here are used as *queries* against Qdrant — asymmetric
            # E5-style models embed differently for queries vs passages.
            embeddings = self.embedding_service.generate_embeddings(
                chunks, is_query=True
            )

            # Display-only hint: phrases that appear in many chunks of the
            # current document are likely templated boilerplate. We pass
            # this set down to the snippet builder so it can centre the
            # extract on the actual planted passage rather than the
            # template wrapper. Detection/scoring are NOT affected.
            boilerplate_ngrams = collect_boilerplate_ngrams(chunks)

            matches: list[dict[str, Any]] = []
            best_scores_by_chunk: list[float] = []
            raw_qdrant_results_count = 0

            metadata_by_index = (
                chunk_metadata
                if isinstance(chunk_metadata, list) and len(chunk_metadata) == len(chunks)
                else [{} for _ in chunks]
            )

            for chunk_index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                similar_chunks = self.vector_service.search_similar_chunks(
                    embedding=embedding,
                    limit=top_k,
                )
                raw_qdrant_results_count += len(similar_chunks)
                suspicious_matches = self._filter_matches(
                    scenario_id=scenario_id,
                    chunk_index=chunk_index,
                    chunk_text=chunk,
                    chunk_metadata=metadata_by_index[chunk_index],
                    similar_chunks=similar_chunks,
                    similarity_threshold=similarity_threshold,
                    excluded_scenario_ids=excluded,
                    boilerplate_ngrams=boilerplate_ngrams,
                )

                matches.extend(suspicious_matches)
                best_scores_by_chunk.append(
                    max(
                        (
                            match["similarity_score"]
                            for match in suspicious_matches
                        ),
                        default=0.0,
                    )
                )

            global_similarity_score = self._calculate_global_similarity_score(
                best_scores_by_chunk
            )
            plagiarism_detected = bool(matches)

            logger.info(
                "Plagiarism analysis completed for scenario_id=%s. "
                "Detected=%s, global_score=%s, matches=%s.",
                scenario_id,
                plagiarism_detected,
                global_similarity_score,
                len(matches),
            )

            result = {
                "scenario_id": scenario_id,
                "global_similarity_score": global_similarity_score,
                "plagiarism_detected": plagiarism_detected,
                "matches": matches,
            }
            if settings.PLAGIARISM_DIAGNOSTICS_ENABLED:
                result["diagnostics"] = {
                    "chunks_analyzed": len(chunks),
                    "raw_qdrant_results_count": raw_qdrant_results_count,
                    "kept_matches": len(matches),
                }
            return result
        except Exception as exc:
            logger.exception(
                "Failed to analyze plagiarism for scenario_id=%s.",
                scenario_id,
            )
            raise RuntimeError("Failed to analyze plagiarism") from exc

    def _filter_matches(
        self,
        scenario_id: str,
        chunk_index: int,
        chunk_text: str,
        chunk_metadata: dict[str, Any] | None,
        similar_chunks: list[dict[str, Any]],
        similarity_threshold: float,
        excluded_scenario_ids: set[str] | None = None,
        boilerplate_ngrams: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter Qdrant results and format suspicious matches."""
        matches: list[dict[str, Any]] = []
        excluded = excluded_scenario_ids or set()
        metadata = chunk_metadata or {}

        for result in similar_chunks:
            payload = result.get("payload") or {}
            matched_scenario_id = payload.get("scenario_id")
            matched_scenario_str = (
                str(matched_scenario_id) if matched_scenario_id is not None else ""
            )
            score = float(result.get("score", 0.0))

            if matched_scenario_str == scenario_id:
                logger.debug(
                    "Ignoring self-match for scenario_id=%s at chunk_index=%s.",
                    scenario_id,
                    chunk_index,
                )
                continue

            if matched_scenario_str and matched_scenario_str in excluded:
                logger.debug(
                    "Ignoring excluded scenario_id=%s (same file/text hash as current).",
                    matched_scenario_str,
                )
                continue

            if score < similarity_threshold:
                continue

            source_chunk_index = payload.get("chunk_index")
            source_page_number = payload.get("page_number")
            current_chunk_id = (
                metadata.get("chunk_id") or f"{scenario_id}_{chunk_index}"
            )
            boilerplate_ratio = max(
                self._to_float(metadata.get("boilerplate_ratio")),
                self._to_float(payload.get("boilerplate_ratio")),
            )
            display_source = (
                payload.get("chunk_text_display")
                or payload.get("chunk_text")
                or ""
            )
            quality = self._match_quality_metrics(
                text=str(display_source),
                similarity_score=score,
                boilerplate_ratio=boilerplate_ratio,
            )
            # The snippet is purely a display concern: locate the actual
            # overlap between the current chunk and the source chunk so the
            # report shows the truly similar passage rather than the chunk
            # header. Scoring/detection above is untouched.
            snippet_info = build_plagiarism_snippet(
                current_text=str(chunk_text or ""),
                source_text=str(display_source),
                fallback_text=str(display_source),
                max_chars=900,
                min_chars=400,
                source_boilerplate_ngrams=boilerplate_ngrams,
            )
            # Composite plagiarism score. The raw cosine on its own is not
            # enough — screenplays share a lot of generic vocabulary that
            # inflates the embedding similarity without indicating real
            # copying. We combine semantic + lexical + exact overlap +
            # dialogue overlap and apply anti-false-positive penalties.
            composite = compute_composite_scores(
                semantic_score=score,
                query_text=str(chunk_text or ""),
                source_text=str(display_source),
            )
            # Garde anti-faux-positifs sémantiques. e5-base rapproche les
            # passages de même registre stylistique (didascalies courtes,
            # même langue partagée, scènes intimistes) même quand aucune
            # phrase n'est réellement copiée. Sans preuve lexicale ET sans
            # n-gramme exact partagé, le "match" n'est qu'une proximité
            # de style — on le drop avant qu'il ne pollue le rapport.
            lexical_evidence = float(composite.get("lexical_score", 0.0) or 0.0)
            exact_evidence = float(composite.get("exact_overlap_score", 0.0) or 0.0)
            dialogue_evidence = float(
                composite.get("dialogue_overlap_score", 0.0) or 0.0
            )
            entity_evidence = float(
                composite.get("named_entity_overlap_score", 0.0) or 0.0
            )
            textual_evidence = lexical_evidence + exact_evidence
            # Micro-overlap accidentel = pas une preuve. On exige un
            # signal substantiel sur au moins un des autres axes.
            if (
                textual_evidence < self.MIN_TEXTUAL_EVIDENCE
                and dialogue_evidence < 0.15
                and entity_evidence < 0.10
            ):
                logger.debug(
                    "Dropping semantic-only match for scenario_id=%s chunk=%s "
                    "(lexical=%.3f, exact=%.3f).",
                    scenario_id,
                    chunk_index,
                    lexical_evidence,
                    exact_evidence,
                )
                continue
            is_false_positive, fp_reason = is_likely_false_positive(composite)
            if is_false_positive:
                # Auparavant on écrasait juste le score à 0.30 et le match
                # restait visible. Mais ces matches n'apportent aucune
                # information utile (style identique, contenu différent) :
                # on les drop pour éviter de polluer le rapport.
                logger.debug(
                    "Dropping false-positive match for scenario_id=%s chunk=%s "
                    "(%s).",
                    scenario_id,
                    chunk_index,
                    fp_reason,
                )
                continue
            snippet = snippet_info["snippet"]
            matches.append(
                {
                    "chunk_index": chunk_index,
                    "current_chunk_index": metadata.get("chunk_index", chunk_index),
                    "current_chunk_id": current_chunk_id,
                    "chunk_text": chunk_text,
                    "snippet": snippet,
                    "snippet_source": snippet_info["snippet_source"],
                    "overlap_text": snippet_info["overlap_text"],
                    "matched_scenario_id": matched_scenario_id,
                    "matched_chunk_id": payload.get("chunk_id"),
                    "source_chunk_id": payload.get("chunk_id"),
                    "source_chunk_index": source_chunk_index,
                    # Propagate source filenames from the Qdrant payload so the
                    # plagiarism pipeline can label each source group with the
                    # real scenario name instead of "non disponible".
                    "original_filename": payload.get("original_filename"),
                    "stored_filename": payload.get("stored_filename"),
                    "filename": payload.get("original_filename"),
                    "matched_chunk_text": payload.get("chunk_text"),
                    "matched_chunk_text_display": (
                        payload.get("chunk_text_display")
                        or payload.get("chunk_text")
                    ),
                    "page_number": source_page_number,
                    "current_page_number": metadata.get("page_number"),
                    "source_page_number": source_page_number,
                    "start_offset": metadata.get("start_offset"),
                    "end_offset": metadata.get("end_offset"),
                    "source_start_offset": payload.get("start_offset"),
                    "source_end_offset": payload.get("end_offset"),
                    "word_count": metadata.get("word_count"),
                    "source_word_count": payload.get("word_count"),
                    "boilerplate_ratio": quality["boilerplate_ratio"],
                    "informative_word_count": quality["informative_word_count"],
                    "match_quality_score": quality["match_quality_score"],
                    "similarity_score": score,
                    "score": score,
                    "semantic_score": composite["semantic_score"],
                    "lexical_score": composite["lexical_score"],
                    "exact_overlap_score": composite["exact_overlap_score"],
                    "named_entity_overlap_score": composite[
                        "named_entity_overlap_score"
                    ],
                    "dialogue_overlap_score": composite["dialogue_overlap_score"],
                    "final_score": composite["final_score"],
                    "raw_score": score,
                    "display_score": format_percent(composite["final_score"]),
                    "risk": risk_from_composite(composite),
                    "is_false_positive": is_false_positive,
                    "debug_reason": fp_reason,
                }
            )

        return matches

    def _match_quality_metrics(
        self,
        text: str,
        similarity_score: float,
        boilerplate_ratio: float = 0.0,
    ) -> dict[str, Any]:
        words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        informative_words = [
            word
            for word in words
            if len(word) > 2 and word not in self.LOW_INFORMATION_WORDS
        ]
        informative_word_count = len(informative_words)
        length_factor = min(1.0, informative_word_count / 35.0)
        boilerplate_penalty = max(0.0, 1.0 - float(boilerplate_ratio or 0.0))
        quality = float(similarity_score or 0.0) * boilerplate_penalty * length_factor
        return {
            "boilerplate_ratio": round(float(boilerplate_ratio or 0.0), 4),
            "informative_word_count": informative_word_count,
            "match_quality_score": round(quality, 4),
        }

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _calculate_global_similarity_score(self, scores: list[float]) -> float:
        """Calculate the average best suspicious score across analyzed chunks."""
        if not scores:
            return 0.0

        return round(sum(scores) / len(scores), 4)

    def _validate_inputs(
        self,
        scenario_id: str,
        chunks: list[str],
        similarity_threshold: float,
        top_k: int,
    ) -> None:
        """Validate plagiarism analysis inputs.

        Args:
            scenario_id: Identifier of the scenario being analyzed.
            chunks: Cleaned text chunks to analyze.
            similarity_threshold: Minimum accepted similarity score.
            top_k: Number of vector search results per chunk.

        Raises:
            ValueError: If an input is invalid.
            TypeError: If chunks is not a list of strings.
        """
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError("scenario_id must not be empty")

        if not isinstance(chunks, list):
            raise TypeError("chunks must be a list of strings")

        if not chunks:
            raise ValueError("chunks must not be empty")

        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, str):
                raise TypeError("all chunks must be strings")

            if not chunk.strip():
                raise ValueError(f"chunk at index {index} must not be empty")

        if not 0 <= similarity_threshold <= 1:
            raise ValueError("similarity_threshold must be between 0 and 1")

        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
    # Somme minimale (lexical + exact_overlap) requise pour qu'un match
    # soit conservé. En-dessous, il n'y a aucune preuve textuelle réelle
    # de copie — seulement une proximité de sens/style captée par e5-base.
    MIN_TEXTUAL_EVIDENCE = 0.10

    LOW_INFORMATION_WORDS = {
        "the", "and", "for", "with", "that", "this", "une", "des", "les",
        "pour", "dans", "avec", "cette", "ligne", "texte", "page", "test",
        "non", "commun", "remplissage",
    }

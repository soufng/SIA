"""Strict-similarity verdict, dedicated to authorization-renewal workflows.

The CCM operator validates renewal requests by checking whether the newly
uploaded scenario is *strictly the same* as one previously approved. This
service produces a single, prominent verdict so the operator can decide at a
glance whether to fast-track the renewal or to treat the upload as a new
analysis.

Verdict ladder
--------------
* ``identical``       — file_hash or cleaned text_hash matches an existing
                         analysis. Same binary file or same cleaned text →
                         direct renewal candidate.
* ``near_identical``  — global Jaccard similarity ≥ 0.95. Layout-only or
                         minor edits.
* ``highly_similar``  — global Jaccard similarity 0.80–0.94. Substantial
                         overlap, requires manual review of the diff.
* ``different``       — no historical scenario crosses the 0.80 threshold.
                         Treat as a new document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pymongo.errors import PyMongoError

from backend.repositories.analysis_repository import AnalysisRepository
from backend.services.local_similarity_service import LocalSimilarityService


logger = logging.getLogger(__name__)


# Tunable thresholds — used by the verdict ladder above. Kept here (not in
# settings) on purpose: changing them affects the semantics of "renewal
# candidate", which we want to be auditable from a single place.
NEAR_IDENTICAL_THRESHOLD = 0.95
HIGHLY_SIMILAR_THRESHOLD = 0.80
# Cap how many historical analyses we Jaccard-compare against. The check is
# O(N) over MongoDB; in practice it's fine up to a few hundreds.
MAX_HISTORY_COMPARED = 200


@dataclass(frozen=True)
class StrictMatchedAnalysis:
    """One historical analysis that the current upload was compared to."""

    scenario_id: str
    original_filename: str | None
    stored_filename: str | None
    analyzed_at: str | None
    risk_level: str | None
    file_hash: str | None
    text_hash: str | None
    similarity_score: float
    match_type: str  # "file_hash" | "text_hash" | "global_jaccard"


@dataclass(frozen=True)
class StrictVerdict:
    """Final verdict that ends up in ``analysis_result["strict_match"]``."""

    verdict: str          # identical | near_identical | highly_similar | different
    score: float          # 0.0–1.0 — best score across historical matches
    match_type: str       # file_hash | text_hash | global_jaccard | none
    is_renewal_candidate: bool
    reason: str
    matched_analysis: StrictMatchedAnalysis | None
    candidates_compared: int
    extras: list[StrictMatchedAnalysis] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _serialize(item: StrictMatchedAnalysis) -> dict[str, Any]:
            return {
                "scenario_id": item.scenario_id,
                "original_filename": item.original_filename,
                "stored_filename": item.stored_filename,
                "analyzed_at": item.analyzed_at,
                "risk_level": item.risk_level,
                "file_hash": item.file_hash,
                "text_hash": item.text_hash,
                "similarity_score": round(item.similarity_score, 4),
                "score_percent": round(item.similarity_score * 100, 2),
                "match_type": item.match_type,
            }

        return {
            "verdict": self.verdict,
            "score": round(self.score, 4),
            "score_percent": round(self.score * 100, 2),
            "match_type": self.match_type,
            "is_renewal_candidate": self.is_renewal_candidate,
            "reason": self.reason,
            "candidates_compared": self.candidates_compared,
            "matched_analysis": (
                _serialize(self.matched_analysis) if self.matched_analysis else None
            ),
            "extras": [_serialize(e) for e in self.extras],
        }


class StrictSimilarityService:
    """Compute a strict-similarity verdict against the analysis history.

    The service is intentionally read-only: it never mutates MongoDB and
    never reaches Qdrant. It relies on three signals only:
      1. ``file_hash`` (SHA-256 of the binary)
      2. ``text_hash`` (SHA-256 of the cleaned text)
      3. global Jaccard similarity of word-shingles
    """

    def __init__(
        self,
        analysis_repository: AnalysisRepository | None = None,
        local_similarity_service: LocalSimilarityService | None = None,
    ) -> None:
        self.analysis_repository = analysis_repository or AnalysisRepository()
        self.local_similarity_service = (
            local_similarity_service or LocalSimilarityService()
        )

    def compute(
        self,
        *,
        current_scenario_id: str,
        current_file_hash: str,
        current_text_hash: str,
        current_cleaned_text: str,
    ) -> StrictVerdict:
        """Return the strict-similarity verdict for the current upload."""
        history = self._load_history(exclude_scenario_id=current_scenario_id)

        # --- Step 1 : exact file/text hash match (cheap, conclusive) ----
        for doc in history:
            doc_file_hash = self._field(doc, "file_hash")
            if doc_file_hash and doc_file_hash == current_file_hash:
                return self._build_verdict(
                    verdict="identical",
                    score=1.0,
                    match_type="file_hash",
                    is_renewal_candidate=True,
                    reason=(
                        "Le fichier PDF est identique au binaire d'une analyse "
                        "déjà validée (même empreinte SHA-256)."
                    ),
                    matched=self._to_matched(
                        doc, 1.0, match_type="file_hash"
                    ),
                    extras=[],
                    candidates_compared=len(history),
                )

        for doc in history:
            doc_text_hash = self._field(doc, "text_hash")
            if doc_text_hash and doc_text_hash == current_text_hash:
                return self._build_verdict(
                    verdict="identical",
                    score=1.0,
                    match_type="text_hash",
                    is_renewal_candidate=True,
                    reason=(
                        "Le texte nettoyé est strictement identique à une "
                        "analyse antérieure (mise en page éventuellement "
                        "différente, contenu textuel identique)."
                    ),
                    matched=self._to_matched(
                        doc, 1.0, match_type="text_hash"
                    ),
                    extras=[],
                    candidates_compared=len(history),
                )

        # --- Step 2 : Jaccard global on word-shingles -----------------
        current_shingles = self.local_similarity_service._word_shingles(  # noqa: SLF001
            current_cleaned_text
        )

        ranked: list[tuple[float, dict[str, Any]]] = []
        for doc in history:
            candidate_text = self._extract_cleaned_text(doc)
            if not candidate_text:
                continue
            candidate_shingles = self.local_similarity_service._word_shingles(  # noqa: SLF001
                candidate_text
            )
            if not candidate_shingles:
                continue
            score = self.local_similarity_service._jaccard_similarity(  # noqa: SLF001
                current_shingles, candidate_shingles
            )
            if score > 0:
                ranked.append((score, doc))

        ranked.sort(key=lambda pair: pair[0], reverse=True)

        if not ranked:
            return self._build_verdict(
                verdict="different",
                score=0.0,
                match_type="none",
                is_renewal_candidate=False,
                reason=(
                    "Aucun scénario antérieur ne dépasse le seuil de "
                    "similarité — à traiter comme une nouvelle demande."
                ),
                matched=None,
                extras=[],
                candidates_compared=len(history),
            )

        best_score, best_doc = ranked[0]
        if best_score >= NEAR_IDENTICAL_THRESHOLD:
            verdict = "near_identical"
            is_renewal = True
            reason = (
                f"Similarité globale {best_score * 100:.2f} % avec une "
                "analyse antérieure : modifications mineures uniquement "
                "(prolongation candidate après contrôle visuel rapide)."
            )
        elif best_score >= HIGHLY_SIMILAR_THRESHOLD:
            verdict = "highly_similar"
            is_renewal = False
            reason = (
                f"Similarité globale {best_score * 100:.2f} % avec une "
                "analyse antérieure : modifications substantielles à "
                "vérifier avant toute décision."
            )
        else:
            verdict = "different"
            is_renewal = False
            reason = (
                f"Meilleure similarité {best_score * 100:.2f} % — en "
                "dessous du seuil de prolongation. À traiter comme une "
                "nouvelle demande."
            )

        matched = self._to_matched(best_doc, best_score, match_type="global_jaccard")
        extras = [
            self._to_matched(doc, score, match_type="global_jaccard")
            for score, doc in ranked[1:4]
        ]

        return self._build_verdict(
            verdict=verdict,
            score=best_score,
            match_type="global_jaccard",
            is_renewal_candidate=is_renewal,
            reason=reason,
            matched=matched,
            extras=extras,
            candidates_compared=len(history),
        )

    # ---------- internals ----------

    def _load_history(
        self, *, exclude_scenario_id: str
    ) -> list[dict[str, Any]]:
        try:
            documents = self.analysis_repository.list_history(
                limit=MAX_HISTORY_COMPARED
            )
        except PyMongoError as exc:
            logger.warning(
                "Strict similarity: history lookup failed (%s). "
                "Verdict will fall back to 'different'.",
                exc,
            )
            return []
        except Exception:  # pragma: no cover - defensive
            logger.exception("Strict similarity: unexpected history error.")
            return []

        filtered: list[dict[str, Any]] = []
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            sid = doc.get("scenario_id")
            if sid and str(sid) == str(exclude_scenario_id):
                continue
            filtered.append(doc)
        return filtered

    @staticmethod
    def _field(doc: dict[str, Any], key: str) -> str | None:
        """Read a field from either the top-level doc or the nested analysis."""
        value = doc.get(key)
        if value:
            return str(value)
        analysis = doc.get("analysis") or doc.get("result")
        if isinstance(analysis, dict):
            nested = analysis.get(key)
            if nested:
                return str(nested)
            stats = analysis.get("document_stats")
            if isinstance(stats, dict) and stats.get(key):
                return str(stats[key])
        return None

    def _extract_cleaned_text(self, doc: dict[str, Any]) -> str:
        """Try several known shapes to reconstruct the cleaned text.

        MongoDB documents don't store the full cleaned text by default — but
        we can fall back to the concatenation of chunks when present.
        """
        analysis = doc.get("analysis") or doc.get("result") or doc
        if not isinstance(analysis, dict):
            return ""
        cleaned = analysis.get("cleaned_text")
        if isinstance(cleaned, str) and cleaned.strip():
            return cleaned
        # Some pipelines store chunks directly.
        chunks = analysis.get("chunks") or analysis.get("text_chunks")
        if isinstance(chunks, list):
            joined = " ".join(c for c in chunks if isinstance(c, str))
            if joined.strip():
                return joined
        # Final fallback: use plagiarism matches' chunk_text (always present
        # when the upload triggered any vector match).
        plagiarism = analysis.get("plagiarism") or {}
        if isinstance(plagiarism, dict):
            buckets: list[str] = []
            for match in plagiarism.get("matches") or []:
                if isinstance(match, dict):
                    text = match.get("chunk_text")
                    if isinstance(text, str):
                        buckets.append(text)
            if buckets:
                return " ".join(buckets)
        return ""

    @staticmethod
    def _to_matched(
        doc: dict[str, Any], score: float, *, match_type: str
    ) -> StrictMatchedAnalysis:
        analysis = doc.get("analysis") or doc.get("result") or doc
        stats: dict[str, Any] = (
            analysis.get("document_stats") if isinstance(analysis, dict) else {}
        ) or {}
        risk = None
        if isinstance(analysis, dict):
            risk = (analysis.get("rag_report") or {}).get("risk_level")
        return StrictMatchedAnalysis(
            scenario_id=str(
                doc.get("scenario_id")
                or (analysis.get("scenario_id") if isinstance(analysis, dict) else "")
                or ""
            ),
            original_filename=(
                doc.get("filename")
                or stats.get("original_filename")
                or stats.get("file_name")
            ),
            stored_filename=(
                doc.get("stored_filename")
                or stats.get("file_name")
            ),
            analyzed_at=(
                doc.get("analysis_timestamp")
                or doc.get("created_at")
            ),
            risk_level=risk,
            file_hash=doc.get("file_hash")
            or (analysis.get("file_hash") if isinstance(analysis, dict) else None),
            text_hash=doc.get("text_hash")
            or (analysis.get("text_hash") if isinstance(analysis, dict) else None),
            similarity_score=float(score),
            match_type=match_type,
        )

    @staticmethod
    def _build_verdict(
        *,
        verdict: str,
        score: float,
        match_type: str,
        is_renewal_candidate: bool,
        reason: str,
        matched: StrictMatchedAnalysis | None,
        extras: list[StrictMatchedAnalysis],
        candidates_compared: int,
    ) -> StrictVerdict:
        return StrictVerdict(
            verdict=verdict,
            score=score,
            match_type=match_type,
            is_renewal_candidate=is_renewal_candidate,
            reason=reason,
            matched_analysis=matched,
            extras=extras,
            candidates_compared=candidates_compared,
        )

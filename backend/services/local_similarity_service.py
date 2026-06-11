import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError

from backend.core.config import settings
from backend.repositories.analysis_repository import AnalysisRepository
from backend.services.chunking_service import ChunkingService
from backend.services.pdf_service import PDFService
from backend.services.text_cleaning_service import TextCleaningService
from backend.utils.text_overlap import (
    build_plagiarism_snippet,
    collect_boilerplate_ngrams,
)


logger = logging.getLogger(__name__)


class LocalSimilarityService:
    """Compare an uploaded PDF with local raw files and saved MongoDB analyses."""

    RAW_DIR = Path("data/raw")
    LOW_INFORMATION_WORDS = {
        "the", "and", "for", "with", "that", "this", "une", "des", "les",
        "pour", "dans", "avec", "cette", "ligne", "texte", "page", "test",
        "non", "commun", "remplissage",
    }

    def __init__(
        self,
        raw_dir: str | Path | None = None,
        pdf_service: PDFService | None = None,
        text_cleaning_service: TextCleaningService | None = None,
        chunking_service: ChunkingService | None = None,
        analysis_repository: AnalysisRepository | None = None,
    ) -> None:
        self.raw_dir = Path(raw_dir or self.RAW_DIR)
        self.pdf_service = pdf_service or PDFService()
        self.text_cleaning_service = text_cleaning_service or TextCleaningService()
        self.chunking_service = chunking_service or ChunkingService()
        self.analysis_repository = analysis_repository or AnalysisRepository()

    def analyze(
        self,
        scenario_id: str,
        current_file_path: str,
        current_text: str,
        current_chunks: list[str],
        file_hash: str,
        text_hash: str,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        """Return local partial matches plus exact duplicate metadata."""
        current_path = Path(current_file_path)
        logger.info("Local similarity current file path: %s", current_path)
        logger.info("Local similarity raw directory: %s", self.raw_dir)
        logger.info("Current chunk count for local similarity: %s", len(current_chunks))

        raw_result = self._compare_raw_files(
            current_path=current_path,
            current_text=current_text,
            current_chunks=current_chunks,
            file_hash=file_hash,
            text_hash=text_hash,
        )
        raw_matches = raw_result["matches"]
        duplicate_analyses = raw_result["duplicate_analyses"]

        mongo_duplicates = self._find_mongodb_duplicates(
            scenario_id=scenario_id,
            file_hash=file_hash,
            text_hash=text_hash,
            original_filename=original_filename,
        )
        duplicate_analyses = self._dedupe_duplicate_analyses(
            duplicate_analyses + mongo_duplicates
        )

        matches = raw_matches
        max_score = max(
            (float(match.get("similarity_score", 0.0)) for match in matches),
            default=0.0,
        )
        exact_duplicate = bool(duplicate_analyses)
        if exact_duplicate:
            max_score = 1.0
        risk = self._risk_from_score(max_score)

        logger.info(
            "Local similarity final score for scenario_id=%s: %s "
            "(risk=%s, matches=%s, exact_duplicates=%s)",
            scenario_id,
            max_score,
            risk,
            len(matches),
            len(duplicate_analyses),
        )

        return {
            "scenario_id": scenario_id,
            "score": round(max_score, 4),
            "score_percent": round(max_score * 100, 2),
            "risk": risk,
            "duplicate": exact_duplicate,
            "exact_duplicate": exact_duplicate,
            "duplicate_count": len(duplicate_analyses),
            "duplicate_analyses": duplicate_analyses,
            "matches": matches,
            "source": "local",
        }

    def compute_file_hash(self, file_path: str | Path) -> str:
        """Return the SHA256 hash for a file."""
        sha256 = hashlib.sha256()
        with Path(file_path).open("rb") as file_obj:
            for block in iter(lambda: file_obj.read(1024 * 1024), b""):
                sha256.update(block)
        return sha256.hexdigest()

    def compute_text_hash(self, text: str) -> str:
        """Return a stable SHA256 hash for cleaned text."""
        normalized = " ".join(text.split()).encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def _compare_raw_files(
        self,
        current_path: Path,
        current_text: str,
        current_chunks: list[str],
        file_hash: str,
        text_hash: str,
    ) -> dict[str, list[dict[str, Any]]]:
        raw_files = self._list_raw_pdfs(current_path)
        logger.info(
            "Comparing against %s raw PDF file(s): %s",
            len(raw_files),
            [path.name for path in raw_files],
        )

        current_shingles = self._word_shingles(current_text)
        matches: list[dict[str, Any]] = []
        duplicate_analyses: list[dict[str, Any]] = []

        for raw_file in raw_files:
            try:
                raw_file_hash = self.compute_file_hash(raw_file)
                if raw_file_hash == file_hash:
                    metadata = self._lookup_mongo_metadata(raw_file_hash)
                    duplicate_analyses.append(
                        self._build_duplicate_analysis(
                            scenario_id=metadata.get("scenario_id"),
                            original_filename=metadata.get("original_filename"),
                            stored_filename=raw_file.name,
                            file_hash=raw_file_hash,
                            text_hash=None,
                            source="raw",
                            created_at=metadata.get("created_at"),
                        )
                    )
                    logger.info(
                        "Exact raw duplicate recorded with %s (file hash match).",
                        raw_file.name,
                    )
                    continue

                candidate_raw_text = self.pdf_service.extract_text(str(raw_file))
                candidate_text_full = self.text_cleaning_service.clean_text(
                    candidate_raw_text
                )
                candidate_text_hash = self.compute_text_hash(candidate_text_full)
                if candidate_text_hash == text_hash:
                    metadata = self._lookup_mongo_metadata(raw_file_hash)
                    duplicate_analyses.append(
                        self._build_duplicate_analysis(
                            scenario_id=metadata.get("scenario_id"),
                            original_filename=metadata.get("original_filename"),
                            stored_filename=raw_file.name,
                            file_hash=raw_file_hash,
                            text_hash=candidate_text_hash,
                            source="raw",
                            created_at=metadata.get("created_at"),
                        )
                    )
                    logger.info(
                        "Exact raw duplicate recorded with %s (text hash match).",
                        raw_file.name,
                    )
                    continue

                candidate_repeated_lines = (
                    self.text_cleaning_service.find_repeated_boilerplate_lines(
                        candidate_text_full
                    )
                )
                if not isinstance(candidate_repeated_lines, set):
                    candidate_repeated_lines = set()
                candidate_boilerplate_ratio = self.text_cleaning_service.boilerplate_ratio(
                    candidate_text_full,
                    candidate_repeated_lines,
                )
                try:
                    candidate_boilerplate_ratio = float(candidate_boilerplate_ratio or 0.0)
                except (TypeError, ValueError):
                    candidate_boilerplate_ratio = 0.0
                candidate_text = candidate_text_full

                similarity = self._jaccard_similarity(
                    current_shingles,
                    self._word_shingles(candidate_text),
                )

                candidate_chunks = self.chunking_service.chunk_text(
                    candidate_text,
                    chunk_size=settings.PLAGIARISM_CHUNK_SIZE,
                    overlap=settings.PLAGIARISM_CHUNK_OVERLAP,
                )
                logger.info("Similarity with raw file %s: %s", raw_file.name, similarity)

                if similarity > 0:
                    metadata = self._lookup_mongo_metadata(raw_file_hash)
                    raw_extracted = candidate_raw_text or self._safe_extract_text(raw_file)
                    display_text = self._build_display_text(raw_extracted)
                    display_chunks = self.chunking_service.chunk_text(
                        display_text if display_text else candidate_text,
                        chunk_size=settings.PLAGIARISM_CHUNK_SIZE,
                        overlap=settings.PLAGIARISM_CHUNK_OVERLAP,
                    )
                    matches.extend(
                        self._build_partial_chunk_matches(
                            raw_file=raw_file,
                            current_chunks=current_chunks,
                            candidate_chunks=candidate_chunks,
                            display_chunks=display_chunks,
                            metadata=metadata,
                            source_file_hash=raw_file_hash,
                            source_text_hash=candidate_text_hash,
                            boilerplate_ratio=candidate_boilerplate_ratio,
                        )
                    )
            except Exception as exc:
                logger.exception("Failed to compare raw file %s: %s", raw_file, exc)

        return {
            "matches": sorted(
                matches,
                key=lambda item: item["similarity_score"],
                reverse=True,
            )[:10],
            "duplicate_analyses": self._dedupe_duplicate_analyses(duplicate_analyses),
        }

    def _find_mongodb_duplicates(
        self,
        scenario_id: str,
        file_hash: str,
        text_hash: str,
        original_filename: str | None,
    ) -> list[dict[str, Any]]:
        try:
            finder = getattr(self.analysis_repository, "find_exact_duplicates", None)
            if callable(finder):
                documents = finder(
                    file_hash=file_hash,
                    text_hash=text_hash,
                    exclude_scenario_id=scenario_id,
                    limit=20,
                )
                if not isinstance(documents, list):
                    documents = self.analysis_repository.find_by_file_hash(
                        file_hash=file_hash,
                        exclude_scenario_id=scenario_id,
                        limit=20,
                    )
            else:
                documents = self.analysis_repository.find_by_file_hash(
                    file_hash=file_hash,
                    exclude_scenario_id=scenario_id,
                    limit=20,
                )
            if not isinstance(documents, list):
                documents = []
        except (AttributeError, PyMongoError) as exc:
            logger.exception("Unable to search MongoDB exact duplicates: %s", exc)
            return []

        duplicates: list[dict[str, Any]] = []
        for document in documents:
            document_stats = document.get("document_stats") or {}
            duplicates.append(
                self._build_duplicate_analysis(
                    scenario_id=document.get("scenario_id"),
                    original_filename=(
                        document.get("original_filename")
                        or document.get("filename")
                        or document_stats.get("original_filename")
                        or original_filename
                    ),
                    stored_filename=(
                        document.get("stored_filename")
                        or document_stats.get("file_name")
                        or document.get("filename")
                    ),
                    file_hash=document.get("file_hash") or file_hash,
                    text_hash=document.get("text_hash") or text_hash,
                    source="mongodb",
                    created_at=(
                        document.get("created_at")
                        or document.get("analysis_timestamp")
                    ),
                )
            )

        if duplicates:
            logger.info(
                "MongoDB exact duplicate analyses: %s",
                [item.get("scenario_id") for item in duplicates],
            )
        return self._dedupe_duplicate_analyses(duplicates)

    def _list_raw_pdfs(self, current_path: Path) -> list[Path]:
        if not self.raw_dir.exists():
            logger.info("Raw directory does not exist: %s", self.raw_dir)
            return []

        current_resolved = current_path.resolve()
        pdfs = []
        for path in self.raw_dir.glob("*.pdf"):
            try:
                if path.resolve() == current_resolved:
                    continue
            except OSError:
                continue
            pdfs.append(path)
        return sorted(pdfs)

    def _build_match(
        self,
        filename: str,
        similarity: float,
        source: str,
        duplicate: bool,
        matched_chunks: int,
        matched_scenario_id: Any,
        matched_text: str,
        query_text: str | None = None,
        reason: str | None = None,
        original_filename: str | None = None,
        stored_filename: str | None = None,
        matched_text_display: str | None = None,
        source_file_hash: str | None = None,
        source_text_hash: str | None = None,
        current_chunk_index: int | str | None = None,
        source_chunk_index: int | str | None = None,
        boilerplate_ratio: float = 0.0,
        source_boilerplate_ngrams: set[str] | None = None,
    ) -> dict[str, Any]:
        similarity = round(float(similarity), 4)
        query_text = query_text or "Le texte du fichier uploade n'est pas disponible."
        matched_text = matched_text or "Le texte du fichier similaire n'est pas disponible."
        display_text = matched_text_display or matched_text
        quality = self._match_quality_metrics(
            text=display_text,
            similarity_score=similarity,
            boilerplate_ratio=boilerplate_ratio,
        )
        # Display-only refinement: centre the snippet on the real overlap
        # between the current and source chunks. Scoring and detection above
        # remain untouched — duplicate flag and similarity score are inputs.
        if duplicate:
            snippet_info = {
                "snippet": display_text,
                "snippet_source": "fallback",
                "overlap_text": None,
            }
        else:
            snippet_info = build_plagiarism_snippet(
                current_text=str(query_text),
                source_text=str(display_text),
                fallback_text=str(display_text),
                max_chars=900,
                min_chars=400,
                source_boilerplate_ngrams=source_boilerplate_ngrams,
            )
        return {
            "filename": filename,
            "original_filename": original_filename,
            "stored_filename": stored_filename or filename,
            "similarity": similarity,
            "similarity_score": similarity,
            "similarity_percent": round(similarity * 100, 2),
            "matched_chunks": matched_chunks,
            "source": source,
            "duplicate": duplicate,
            "match_type": "exact_duplicate" if duplicate else "partial_similarity",
            "reason": reason or (
                "Exact same PDF file hash." if duplicate else "Partial similarity match."
            ),
            "matched_scenario_id": matched_scenario_id,
            "matched_chunk_id": filename,
            "current_chunk_id": (
                f"current_{current_chunk_index}"
                if current_chunk_index is not None
                else None
            ),
            "source_chunk_id": (
                f"{filename}_{source_chunk_index}"
                if source_chunk_index is not None
                else filename
            ),
            "current_chunk_index": current_chunk_index,
            "source_chunk_index": source_chunk_index,
            "matched_chunk_text": matched_text,
            "matched_chunk_text_display": display_text,
            "query_text": query_text,
            "matched_text": matched_text,
            "matched_text_display": display_text,
            "snippet": snippet_info["snippet"] or display_text,
            "snippet_source": snippet_info["snippet_source"],
            "overlap_text": snippet_info["overlap_text"],
            "chunk_text": query_text,
            "similar_text": display_text,
            "chunk_index": "file",
            "source_file_hash": source_file_hash,
            "source_text_hash": source_text_hash,
            "boilerplate_ratio": quality["boilerplate_ratio"],
            "informative_word_count": quality["informative_word_count"],
            "match_quality_score": quality["match_quality_score"],
        }

    def _build_partial_chunk_matches(
        self,
        raw_file: Path,
        current_chunks: list[str],
        candidate_chunks: list[str],
        display_chunks: list[str],
        metadata: dict[str, Any],
        source_file_hash: str,
        source_text_hash: str,
        boilerplate_ratio: float = 0.0,
    ) -> list[dict[str, Any]]:
        # Pre-compute boilerplate hint for snippet centring. Display only —
        # has zero effect on the jaccard similarity computed below.
        source_boilerplate_ngrams = collect_boilerplate_ngrams(candidate_chunks)
        matches: list[dict[str, Any]] = []
        for current_index, current_chunk in enumerate(current_chunks):
            current_shingles = self._word_shingles(current_chunk)
            if not current_shingles:
                continue
            for source_index, candidate_chunk in enumerate(candidate_chunks):
                score = self._jaccard_similarity(
                    current_shingles,
                    self._word_shingles(candidate_chunk),
                )
                if score < settings.PLAGIARISM_MIN_MATCH_SCORE:
                    continue
                display = (
                    display_chunks[source_index]
                    if source_index < len(display_chunks)
                    else candidate_chunk
                )
                matches.append(
                    self._build_match(
                        filename=raw_file.name,
                        similarity=score,
                        source="raw",
                        duplicate=False,
                        matched_chunks=1,
                        matched_scenario_id=metadata.get("scenario_id"),
                        matched_text=self._preview(candidate_chunk),
                        matched_text_display=self._preview(display),
                        query_text=self._preview(current_chunk),
                        reason="Partial chunk similarity match.",
                        original_filename=metadata.get("original_filename"),
                        stored_filename=raw_file.name,
                        source_file_hash=source_file_hash,
                        source_text_hash=source_text_hash,
                        source_boilerplate_ngrams=source_boilerplate_ngrams,
                        current_chunk_index=current_index,
                        source_chunk_index=source_index,
                        boilerplate_ratio=boilerplate_ratio,
                    )
                )
        return sorted(matches, key=lambda item: item["similarity_score"], reverse=True)

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
        quality = float(similarity_score or 0.0) * (1.0 - boilerplate_ratio) * length_factor
        return {
            "boilerplate_ratio": round(float(boilerplate_ratio or 0.0), 4),
            "informative_word_count": informative_word_count,
            "match_quality_score": round(max(0.0, quality), 4),
        }

    def _build_duplicate_analysis(
        self,
        scenario_id: Any,
        original_filename: Any,
        stored_filename: Any,
        file_hash: Any,
        text_hash: Any,
        source: str,
        created_at: Any = None,
    ) -> dict[str, Any]:
        return {
            "scenario_id": str(scenario_id) if scenario_id else None,
            "original_filename": str(original_filename) if original_filename else None,
            "stored_filename": str(stored_filename) if stored_filename else None,
            "created_at": str(created_at) if created_at else None,
            "file_hash": str(file_hash) if file_hash else None,
            "text_hash": str(text_hash) if text_hash else None,
            "source": source,
        }

    def _dedupe_duplicate_analyses(
        self,
        duplicates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduped: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
        for duplicate in duplicates:
            key = (
                duplicate.get("scenario_id"),
                duplicate.get("stored_filename"),
                duplicate.get("file_hash"),
                duplicate.get("text_hash"),
            )
            if key in deduped:
                existing = deduped[key]
                for field, value in duplicate.items():
                    if existing.get(field) in (None, "") and value not in (None, ""):
                        existing[field] = value
                continue
            deduped[key] = dict(duplicate)
        return list(deduped.values())

    def _safe_extract_text(self, file_path: Path) -> str:
        """Extract raw text from a PDF, swallowing errors for display purposes."""
        try:
            return self.pdf_service.extract_text(str(file_path)) or ""
        except Exception:
            logger.debug(
                "Display text extraction failed for %s; falling back to cleaned text.",
                file_path,
                exc_info=True,
            )
            return ""

    def _build_display_text(self, raw_text: str) -> str:
        if not isinstance(raw_text, str) or not raw_text:
            return ""
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", raw_text)
        text = text.replace("\t", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _lookup_mongo_metadata(self, raw_file_hash: str) -> dict[str, Any]:
        """Best-effort lookup of an existing MongoDB analysis for this raw file."""
        if not raw_file_hash:
            return {}

        try:
            documents = self.analysis_repository.find_by_file_hash(
                file_hash=raw_file_hash,
                exclude_scenario_id=None,
                limit=1,
            )
        except (AttributeError, PyMongoError):
            logger.debug(
                "MongoDB metadata lookup failed for raw file hash %s.",
                raw_file_hash,
                exc_info=True,
            )
            return {}

        if not documents:
            return {}

        document = documents[0]
        document_stats = document.get("document_stats") or {}
        original_filename = (
            document.get("filename")
            or document.get("original_filename")
            or document_stats.get("original_filename")
        )
        return {
            "scenario_id": document.get("scenario_id"),
            "original_filename": original_filename,
            "created_at": document.get("created_at") or document.get("analysis_timestamp"),
        }

    def _first_chunk_text(
        self,
        chunks: list[str],
        fallback: str = "Le premier chunk n'est pas disponible.",
    ) -> str:
        for chunk in chunks:
            if isinstance(chunk, str) and chunk.strip():
                return self._preview(chunk)
        return fallback

    def _word_shingles(self, text: str, size: int = 5) -> set[str]:
        words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        if not words:
            return set()
        if len(words) < size:
            return {" ".join(words)}
        return {
            " ".join(words[index : index + size])
            for index in range(len(words) - size + 1)
        }

    def _jaccard_similarity(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return round(len(left & right) / len(left | right), 4)

    def _count_matching_chunks(
        self,
        current_chunks: list[str],
        candidate_chunks: list[str],
    ) -> int:
        candidate_set = {" ".join(chunk.split()).lower() for chunk in candidate_chunks}
        return sum(
            1
            for chunk in current_chunks
            if " ".join(chunk.split()).lower() in candidate_set
        )

    def _risk_from_score(self, score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    def _preview(self, text: str, limit: int = 800) -> str:
        compact = " ".join(text.split())
        return compact[:limit]

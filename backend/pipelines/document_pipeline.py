"""Document ingestion pipeline.

Takes a PDF file path and produces everything downstream stages need to
work with the document: cleaned + display text, per-page records, chunks
with metadata, and a ``document_stats`` summary.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.config import settings
from backend.services.chunking_service import ChunkingService
from backend.services.local_similarity_service import LocalSimilarityService
from backend.services.pdf_service import PDFService
from backend.services.text_cleaning_service import TextCleaningService


logger = logging.getLogger(__name__)


_DISPLAY_TEXT_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)


@dataclass
class DocumentContext:
    """Output of the document pipeline, consumed by every downstream stage."""

    scenario_id: str
    file_path: str
    original_filename: str | None
    file_hash: str
    text_hash: str
    raw_text: str
    cleaned_text: str
    display_text: str
    repeated_lines: set[str]
    chunks: list[str]
    display_chunks: list[str]
    chunk_metadata: list[dict[str, Any]] = field(default_factory=list)
    document_stats: dict[str, Any] = field(default_factory=dict)
    page_records: list[dict[str, Any]] = field(default_factory=list)


class DocumentPipeline:
    """Extract, clean, chunk and statistically describe a PDF document."""

    def __init__(
        self,
        pdf_service: PDFService,
        text_cleaning_service: TextCleaningService,
        chunking_service: ChunkingService,
        local_similarity_service: LocalSimilarityService,
    ) -> None:
        self.pdf_service = pdf_service
        self.text_cleaning_service = text_cleaning_service
        self.chunking_service = chunking_service
        self.local_similarity_service = local_similarity_service

    def run(
        self,
        scenario_id: str,
        file_path: str,
        chunk_size: int = settings.PLAGIARISM_CHUNK_SIZE,
        overlap: int = settings.PLAGIARISM_CHUNK_OVERLAP,
        original_filename: str | None = None,
    ) -> DocumentContext:
        logger.info("DocumentPipeline: ingesting %s", file_path)

        file_hash = self.local_similarity_service.compute_file_hash(file_path)
        raw_text = self.pdf_service.extract_text(file_path)
        cleaned_text = self.text_cleaning_service.clean_text(raw_text)
        text_hash = self.local_similarity_service.compute_text_hash(cleaned_text)
        repeated_lines = self.text_cleaning_service.find_repeated_boilerplate_lines(
            cleaned_text
        )
        display_text = self._build_display_text(raw_text)

        page_records = self._build_page_records(
            file_path=file_path,
            fallback_raw_text=raw_text,
            repeated_lines=repeated_lines,
        )
        chunks, display_chunks, chunk_metadata = self._build_chunks(
            page_records=page_records,
            cleaned_text=cleaned_text,
            display_text=display_text,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        if not chunks:
            raise ValueError("PDF contains no analysable text after cleaning")

        document_stats = self._build_document_stats(
            file_path=file_path,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            chunks=chunks,
            original_filename=original_filename,
            file_hash=file_hash,
            text_hash=text_hash,
        )

        return DocumentContext(
            scenario_id=scenario_id,
            file_path=file_path,
            original_filename=original_filename,
            file_hash=file_hash,
            text_hash=text_hash,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            display_text=display_text,
            repeated_lines=repeated_lines,
            chunks=chunks,
            display_chunks=display_chunks,
            chunk_metadata=chunk_metadata,
            document_stats=document_stats,
            page_records=page_records,
        )

    # ---------- Building blocks ----------

    def _build_chunks(
        self,
        page_records: list[dict[str, Any]],
        cleaned_text: str,
        display_text: str,
        chunk_size: int,
        overlap: int,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        chunk_metadata = self.chunking_service.chunk_pages_with_metadata(
            pages=page_records,
            chunk_size=chunk_size,
            overlap=overlap,
            min_chunk_size=settings.PLAGIARISM_MIN_CHUNK_SIZE,
        )
        if chunk_metadata:
            chunks = [str(c["text_normalized"]) for c in chunk_metadata]
            display_chunks = [str(c["text_display"]) for c in chunk_metadata]
            return chunks, display_chunks, chunk_metadata

        # Fallback: page-level extraction failed or produced empty records.
        pairs = self.chunking_service.chunk_text_with_display(
            text_normalized=cleaned_text,
            text_display=display_text,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        chunks = [pair[0] for pair in pairs]
        display_chunks = [pair[1] for pair in pairs]
        chunk_metadata = [
            {
                "chunk_id": f"chunk_{index}",
                "chunk_index": index,
                "page_number": None,
                "start_offset": None,
                "end_offset": None,
                "text_normalized": normalized,
                "text_display": display,
                "raw_text": display,
                "word_count": len(normalized.split()),
            }
            for index, (normalized, display) in enumerate(pairs)
        ]
        return chunks, display_chunks, chunk_metadata

    def _build_page_records(
        self,
        file_path: str,
        fallback_raw_text: str,
        repeated_lines: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        extractor = getattr(self.pdf_service, "extract_pages", None)
        try:
            raw_pages = extractor(file_path) if callable(extractor) else []
        except Exception:
            logger.debug(
                "Page-level extraction unavailable; using full document.",
                exc_info=True,
            )
            raw_pages = []

        if not raw_pages:
            raw_pages = [{"page_number": None, "text": fallback_raw_text}]

        records: list[dict[str, Any]] = []
        for page in raw_pages:
            if not isinstance(page, dict):
                continue
            raw_page_text = str(page.get("text") or "")
            display = self._build_display_text(raw_page_text)
            normalized = self.text_cleaning_service.clean_text(raw_page_text)
            boilerplate_ratio = self.text_cleaning_service.boilerplate_ratio(
                normalized,
                repeated_lines or set(),
            )
            if not normalized.strip():
                continue
            records.append(
                {
                    "page_number": page.get("page_number"),
                    "text_normalized": normalized,
                    "text_display": display or normalized,
                    "boilerplate_ratio": boilerplate_ratio,
                }
            )
        return records

    def _build_document_stats(
        self,
        file_path: str,
        raw_text: str,
        cleaned_text: str,
        chunks: list[str],
        original_filename: str | None,
        file_hash: str | None,
        text_hash: str | None,
    ) -> dict[str, Any]:
        path = Path(file_path)
        return {
            "file_name": path.name,
            "original_filename": original_filename or path.name,
            "file_hash": file_hash,
            "text_hash": text_hash,
            "raw_characters_count": len(raw_text),
            "cleaned_characters_count": len(cleaned_text),
            "words_count": len(cleaned_text.split()),
            "chunks_count": len(chunks),
        }

    @staticmethod
    def _build_display_text(raw_text: str) -> str:
        """Return a lightly cleaned text that preserves accents and casing.

        Only collapses whitespace and strips invisible control characters.
        Unlike ``TextCleaningService.clean_text`` it does *not* apply NFKC
        normalization, so accented glyphs that some PDFs already store as
        precomposed characters stay exactly as extracted.
        """
        if not isinstance(raw_text, str) or not raw_text:
            return ""
        text = _DISPLAY_TEXT_CONTROL_RE.sub("", raw_text)
        text = text.replace("\t", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

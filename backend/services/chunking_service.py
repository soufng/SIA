import logging
import re
from typing import Any

from backend.core.config import settings


logger = logging.getLogger(__name__)


class ChunkingService:
    """Service responsible for splitting cleaned text into word-based chunks."""

    DEFAULT_CHUNK_SIZE = settings.PLAGIARISM_CHUNK_SIZE
    DEFAULT_OVERLAP = settings.PLAGIARISM_CHUNK_OVERLAP
    DEFAULT_MIN_CHUNK_SIZE = settings.PLAGIARISM_MIN_CHUNK_SIZE

    def chunk_text(
        self,
        text: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> list[str]:
        """Split text into ordered chunks with word overlap.

        Args:
            text: Cleaned text to split into chunks.
            chunk_size: Maximum number of words per chunk.
            overlap: Number of words repeated between two consecutive chunks.

        Returns:
            A list of non-empty text chunks, preserving the original word order.

        Raises:
            ValueError: If chunk_size is less than or equal to zero, overlap is
                negative, or overlap is greater than or equal to chunk_size.
            TypeError: If text is not a string.
        """
        self._validate_parameters(text=text, chunk_size=chunk_size, overlap=overlap)

        words = text.split()
        if not words:
            logger.info("Received empty text for chunking.")
            return []

        logger.info(
            "Starting text chunking with chunk_size=%s and overlap=%s.",
            chunk_size,
            overlap,
        )

        chunks: list[str] = []
        step = chunk_size - overlap
        start = 0

        while start < len(words):
            end = start + chunk_size
            chunk_words = words[start:end]

            if chunk_words:
                chunks.append(" ".join(chunk_words))

            start += step

        logger.info("Text chunking completed. Generated %s chunks.", len(chunks))
        return chunks

    def chunk_text_with_display(
        self,
        text_normalized: str,
        text_display: str | None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> list[tuple[str, str]]:
        """Split a text into chunks while keeping a parallel display version.

        The normalized text is the canonical input used by similarity/embedding
        pipelines. The display text is an accent-preserving variant chunked at
        the *same word boundaries*, used only for human-readable extracts.

        If the word counts differ (e.g. the display version went through a
        different cleaning step), the display fallback is the normalized chunk
        itself — we never break the analysis pipeline for the sake of display.

        Args:
            text_normalized: Cleaned/normalized text used for analysis.
            text_display: Original or lightly cleaned text used for display.
            chunk_size: Maximum number of words per chunk.
            overlap: Number of overlapping words between consecutive chunks.

        Returns:
            List of (normalized_chunk, display_chunk) tuples. Both lists have
            the same length and ordering as ``chunk_text(text_normalized)``.
        """
        normalized_chunks = self.chunk_text(
            text_normalized, chunk_size=chunk_size, overlap=overlap
        )
        if not normalized_chunks:
            return []

        if text_display is None or not isinstance(text_display, str):
            return [(chunk, chunk) for chunk in normalized_chunks]

        words_norm = text_normalized.split()
        words_display = text_display.split()

        if len(words_norm) != len(words_display):
            logger.info(
                "chunk_text_with_display: word counts differ "
                "(normalized=%s, display=%s); falling back to normalized chunks "
                "for display.",
                len(words_norm),
                len(words_display),
            )
            return [(chunk, chunk) for chunk in normalized_chunks]

        pairs: list[tuple[str, str]] = []
        step = chunk_size - overlap
        start = 0
        chunk_index = 0
        while start < len(words_norm) and chunk_index < len(normalized_chunks):
            end = start + chunk_size
            display_chunk = " ".join(words_display[start:end])
            pairs.append((normalized_chunks[chunk_index], display_chunk))
            start += step
            chunk_index += 1
        return pairs

    def chunk_pages_with_metadata(
        self,
        pages: list[dict[str, Any]],
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
    ) -> list[dict[str, Any]]:
        """Split page records into metadata-rich chunks.

        Pages are split by paragraph first. Long paragraphs are split by word
        windows. Small adjacent paragraphs are merged so chunks are useful
        enough for semantic search without swallowing unrelated passages.
        """
        if not isinstance(pages, list):
            raise TypeError("pages must be a list")
        self._validate_parameters(text="", chunk_size=chunk_size, overlap=overlap)
        if min_chunk_size <= 0:
            raise ValueError("min_chunk_size must be greater than 0")

        chunks: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_number = page.get("page_number")
            normalized = str(page.get("text_normalized") or "")
            display = str(page.get("text_display") or normalized)
            boilerplate_ratio = float(page.get("boilerplate_ratio") or 0.0)
            chunks.extend(
                self._chunk_single_page(
                    page_number=page_number,
                    text_normalized=normalized,
                    text_display=display,
                    chunk_size=chunk_size,
                    overlap=overlap,
                    min_chunk_size=min_chunk_size,
                    start_index=len(chunks),
                    boilerplate_ratio=boilerplate_ratio,
                )
            )

        for index, chunk in enumerate(chunks):
            chunk["chunk_index"] = index
            chunk["chunk_id"] = f"chunk_{index}"
        return chunks

    def _chunk_single_page(
        self,
        page_number: Any,
        text_normalized: str,
        text_display: str,
        chunk_size: int,
        overlap: int,
        min_chunk_size: int,
        start_index: int,
        boilerplate_ratio: float = 0.0,
    ) -> list[dict[str, Any]]:
        words_normalized = text_normalized.split()
        words_display = text_display.split()
        if not words_normalized:
            return []

        display_aligned = len(words_normalized) == len(words_display)
        paragraph_spans = self._paragraph_word_spans(text_normalized)
        merged_spans = self._merge_small_spans(
            paragraph_spans=paragraph_spans,
            total_words=len(words_normalized),
            min_chunk_size=min_chunk_size,
            chunk_size=chunk_size,
        )

        chunks: list[dict[str, Any]] = []
        for span_start, span_end in merged_spans:
            span_length = span_end - span_start
            if span_length <= 0:
                continue
            if span_length <= chunk_size:
                chunks.append(
                    self._build_metadata_chunk(
                        page_number=page_number,
                        chunk_index=start_index + len(chunks),
                        words_normalized=words_normalized,
                        words_display=words_display,
                        display_aligned=display_aligned,
                        start_word=span_start,
                        end_word=span_end,
                        boilerplate_ratio=boilerplate_ratio,
                    )
                )
                continue

            step = chunk_size - overlap
            start = span_start
            while start < span_end:
                end = min(start + chunk_size, span_end)
                if end - start < min_chunk_size and chunks:
                    previous = chunks[-1]
                    previous_end = min(span_end, previous["_end_word"] + (end - start))
                    chunks[-1] = self._build_metadata_chunk(
                        page_number=page_number,
                        chunk_index=previous["chunk_index"],
                        words_normalized=words_normalized,
                        words_display=words_display,
                        display_aligned=display_aligned,
                        start_word=previous["_start_word"],
                        end_word=previous_end,
                        boilerplate_ratio=boilerplate_ratio,
                    )
                    break
                chunks.append(
                    self._build_metadata_chunk(
                        page_number=page_number,
                        chunk_index=start_index + len(chunks),
                        words_normalized=words_normalized,
                        words_display=words_display,
                        display_aligned=display_aligned,
                        start_word=start,
                        end_word=end,
                        boilerplate_ratio=boilerplate_ratio,
                    )
                )
                if end >= span_end:
                    break
                start += step
        for chunk in chunks:
            chunk.pop("_start_word", None)
            chunk.pop("_end_word", None)
        return chunks

    def _paragraph_word_spans(self, text: str) -> list[tuple[int, int]]:
        paragraphs = [p for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if len(paragraphs) <= 1:
            words = text.split()
            return [(0, len(words))] if words else []

        spans: list[tuple[int, int]] = []
        cursor = 0
        for paragraph in paragraphs:
            count = len(paragraph.split())
            if count:
                spans.append((cursor, cursor + count))
                cursor += count
        return spans

    def _merge_small_spans(
        self,
        paragraph_spans: list[tuple[int, int]],
        total_words: int,
        min_chunk_size: int,
        chunk_size: int,
    ) -> list[tuple[int, int]]:
        if not paragraph_spans:
            return [(0, total_words)] if total_words else []

        merged: list[tuple[int, int]] = []
        current_start, current_end = paragraph_spans[0]
        for start, end in paragraph_spans[1:]:
            current_len = current_end - current_start
            candidate_len = end - current_start
            if current_len < min_chunk_size or candidate_len <= chunk_size:
                current_end = end
                continue
            merged.append((current_start, current_end))
            current_start, current_end = start, end
        merged.append((current_start, current_end))
        return merged

    def _build_metadata_chunk(
        self,
        page_number: Any,
        chunk_index: int,
        words_normalized: list[str],
        words_display: list[str],
        display_aligned: bool,
        start_word: int,
        end_word: int,
        boilerplate_ratio: float = 0.0,
    ) -> dict[str, Any]:
        normalized_words = words_normalized[start_word:end_word]
        display_words = (
            words_display[start_word:end_word] if display_aligned else normalized_words
        )
        text_normalized = " ".join(normalized_words)
        text_display = " ".join(display_words)
        return {
            "chunk_id": f"chunk_{chunk_index}",
            "chunk_index": chunk_index,
            "page_number": page_number,
            "start_offset": start_word,
            "end_offset": end_word,
            "text_normalized": text_normalized,
            "text_display": text_display,
            "raw_text": text_display,
            "word_count": len(normalized_words),
            "boilerplate_ratio": round(float(boilerplate_ratio or 0.0), 4),
            "_start_word": start_word,
            "_end_word": end_word,
        }

    def _validate_parameters(self, text: str, chunk_size: int, overlap: int) -> None:
        """Validate chunking input parameters.

        Args:
            text: Text value received by the chunking service.
            chunk_size: Maximum number of words per chunk.
            overlap: Number of overlapping words between chunks.

        Raises:
            ValueError: If chunk_size or overlap values are invalid.
            TypeError: If text is not a string.
        """
        if not isinstance(text, str):
            logger.error("Invalid text type for chunking: %s", type(text).__name__)
            raise TypeError("text must be a string")

        if chunk_size <= 0:
            logger.error("Invalid chunk_size value: %s", chunk_size)
            raise ValueError("chunk_size must be greater than 0")

        if overlap < 0:
            logger.error("Invalid overlap value: %s", overlap)
            raise ValueError("overlap must be greater than or equal to 0")

        if overlap >= chunk_size:
            logger.error(
                "Invalid overlap=%s for chunk_size=%s. Overlap must be smaller.",
                overlap,
                chunk_size,
            )
            raise ValueError("overlap must be smaller than chunk_size")

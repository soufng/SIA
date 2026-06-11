import logging
import re
import unicodedata
from collections import Counter

from backend.core.config import settings


logger = logging.getLogger(__name__)


class TextCleaningService:
    """Service responsible for cleaning and normalizing extracted text."""

    _CONTROL_CHARACTERS_PATTERN = re.compile(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
    )
    _MULTIPLE_SPACES_PATTERN = re.compile(r"[ ]{2,}")
    _MULTIPLE_NEWLINES_PATTERN = re.compile(r"\n{2,}")
    _PAGE_NUMBER_PATTERN = re.compile(r"^(page\s*)?\d+(/\d+)?$", re.IGNORECASE)

    def clean_text(self, text: str | None) -> str:
        """Clean and normalize text extracted from a PDF.

        Args:
            text: Raw text extracted from a PDF file.

        Returns:
            Cleaned and normalized text. Returns an empty string when the input
            is None or contains only whitespace.
        """
        if text is None:
            logger.warning("Received None text for cleaning.")
            return ""

        if not text.strip():
            logger.info("Received empty text for cleaning.")
            return ""

        logger.info("Starting text cleaning.")

        cleaned_text = unicodedata.normalize("NFKC", text)
        cleaned_text = self._CONTROL_CHARACTERS_PATTERN.sub("", cleaned_text)
        cleaned_text = cleaned_text.replace("\t", " ")
        cleaned_text = re.sub(r"[ \r\f\v]*\n[ \r\f\v]*", "\n", cleaned_text)
        cleaned_text = self._MULTIPLE_NEWLINES_PATTERN.sub("\n", cleaned_text)
        cleaned_text = self._MULTIPLE_SPACES_PATTERN.sub(" ", cleaned_text)
        cleaned_text = cleaned_text.strip()

        logger.info("Text cleaning completed.")
        return cleaned_text

    def find_repeated_boilerplate_lines(
        self,
        text: str,
        min_count: int = settings.BOILERPLATE_REPEATED_LINE_MIN_COUNT,
        min_length: int = settings.BOILERPLATE_REPEATED_LINE_MIN_LENGTH,
        max_length: int = settings.BOILERPLATE_REPEATED_LINE_MAX_LENGTH,
    ) -> set[str]:
        """Return normalized repeated lines that are likely low-value boilerplate."""
        if not isinstance(text, str) or not text.strip():
            return set()

        candidates = [
            normalized
            for line in text.splitlines()
            if (normalized := self._normalize_boilerplate_line(line))
            and self._is_boilerplate_candidate(
                normalized,
                min_length=min_length,
                max_length=max_length,
            )
        ]
        counts = Counter(candidates)
        return {line for line, count in counts.items() if count >= min_count}

    def remove_boilerplate_lines(
        self,
        text: str,
        repeated_lines: set[str],
    ) -> str:
        """Return text with repeated boilerplate lines removed when explicitly requested."""
        if not isinstance(text, str) or not text.strip() or not repeated_lines:
            return text or ""

        kept: list[str] = []
        for line in text.splitlines():
            normalized = self._normalize_boilerplate_line(line)
            if normalized and normalized in repeated_lines:
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def boilerplate_ratio(self, text: str, repeated_lines: set[str]) -> float:
        if not isinstance(text, str) or not text.strip() or not repeated_lines:
            return 0.0
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return 0.0
        boilerplate = sum(
            1
            for line in lines
            if self._normalize_boilerplate_line(line) in repeated_lines
        )
        return round(boilerplate / len(lines), 4)

    def _normalize_boilerplate_line(self, line: str) -> str:
        text = unicodedata.normalize("NFKC", str(line or ""))
        text = self._CONTROL_CHARACTERS_PATTERN.sub("", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text

    def _is_boilerplate_candidate(
        self,
        line: str,
        min_length: int,
        max_length: int,
    ) -> bool:
        if self._PAGE_NUMBER_PATTERN.match(line):
            return True
        if len(line) < min_length or len(line) > max_length:
            return False
        words = re.findall(r"\w+", line, flags=re.UNICODE)
        if len(words) < 3:
            return False
        unique_ratio = len(set(words)) / max(len(words), 1)
        return unique_ratio < 0.95 or len(words) <= 30

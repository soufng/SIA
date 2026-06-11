"""Moderation pipeline.

Wraps profanity and adult-content scoring around a cleaned text. Also
attaches a ``page_number`` to every detected match by looking the snippet
up in the per-page text records produced by ``DocumentPipeline``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from backend.services.adult_content_service import AdultContentService
from backend.services.profanity_service import ProfanityService


logger = logging.getLogger(__name__)


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ModerationOutcome:
    profanity_result: dict[str, Any]
    adult_content_result: dict[str, Any]


class ModerationPipeline:
    def __init__(
        self,
        profanity_service: ProfanityService,
        adult_content_service: AdultContentService,
    ) -> None:
        self.profanity_service = profanity_service
        self.adult_content_service = adult_content_service

    def run(
        self,
        cleaned_text: str,
        page_records: list[dict[str, Any]] | None = None,
    ) -> ModerationOutcome:
        """Score profanity + adult content and attach page numbers.

        Args:
            cleaned_text: The cleaned full-document text fed to the
                moderation lexicons.
            page_records: Optional list of ``{page_number, text_normalized,
                text_display, …}`` records produced by ``DocumentPipeline``.
                When provided, each detected match is tagged with the
                ``page_number`` whose normalized text contains it.
        """
        logger.info("ModerationPipeline: scoring profanity + adult content.")
        profanity_result = self.profanity_service.analyze_text(cleaned_text)
        adult_content_result = self.adult_content_service.analyze_text(cleaned_text)

        if page_records:
            page_texts = self._build_page_lookup(page_records)
            self._attach_page_numbers(
                profanity_result.get("vulgarity_matches"), page_texts
            )
            self._attach_page_numbers(
                adult_content_result.get("nudity_matches"), page_texts
            )

        return ModerationOutcome(
            profanity_result=profanity_result,
            adult_content_result=adult_content_result,
        )

    # ---------- Page tagging ----------

    @staticmethod
    def _build_page_lookup(
        page_records: list[dict[str, Any]],
    ) -> list[tuple[Any, str]]:
        """Pre-build a list of (page_number, lowercased normalized text)."""
        out: list[tuple[Any, str]] = []
        for record in page_records:
            if not isinstance(record, dict):
                continue
            text = str(record.get("text_normalized") or "")
            if not text:
                continue
            out.append((record.get("page_number"), text.casefold()))
        return out

    def _attach_page_numbers(
        self,
        matches: Any,
        page_texts: list[tuple[Any, str]],
    ) -> None:
        if not isinstance(matches, list):
            return
        for match in matches:
            if not isinstance(match, dict):
                continue
            page = self._locate_page_for_match(match, page_texts)
            if page is not None:
                match["page_number"] = page

    @staticmethod
    def _locate_page_for_match(
        match: dict[str, Any],
        page_texts: list[tuple[Any, str]],
    ) -> Any:
        """Return the page_number whose normalized text contains the match.

        Strategy:
        1. Look up the snippet (most reliable, longest substring).
        2. Fall back to the detected word itself.
        3. Give up and return ``None`` if neither is found.
        """
        snippet = str(match.get("snippet") or "")
        # Strip ellipsis markers that ``extract_context_snippet`` prepends
        # when the snippet is cut from the middle of the text.
        snippet = snippet.replace("…", " ").replace("...", " ")
        snippet = _WHITESPACE_RE.sub(" ", snippet).strip().casefold()
        word = str(match.get("word") or match.get("term") or "").strip().casefold()

        for needle in (snippet, word):
            if not needle or len(needle) < 2:
                continue
            for page_number, page_text in page_texts:
                if needle in page_text:
                    return page_number
        return None

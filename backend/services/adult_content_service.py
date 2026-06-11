import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.services.profanity_service import extract_context_snippet


logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class WeightedTerm:
    """Adult-content lexicon term with a severity weight."""

    term: str
    normalized_term: str
    weight: int
    category: str
    language: str


class AdultContentService:
    """Service responsible for detecting adult or sexually explicit content."""

    DEFAULT_FRENCH_LIST_PATH = Path("data/moderation_lists/adult_fr.json")
    DEFAULT_ARABIC_LIST_PATH = Path("data/moderation_lists/adult_ar.json")
    DEFAULT_DARIJA_LIST_PATH = Path("data/moderation_lists/adult_darija.json")
    _ARABIC_DIACRITICS_PATTERN = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
    _WORD_COUNT_PATTERN = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)

    def __init__(
        self,
        french_list_path: str | Path | None = None,
        arabic_list_path: str | Path | None = None,
        darija_list_path: str | Path | None = None,
        lexicon_paths: list[str | Path] | None = None,
    ) -> None:
        """Initialize the service and load adult-content terms.

        Args:
            french_list_path: Path to the French adult-content JSON list.
            arabic_list_path: Path to the Arabic adult-content JSON list.
            darija_list_path: Path to the Darija adult-content JSON list.
            lexicon_paths: Optional explicit list of lexicon files to load.

        Raises:
            FileNotFoundError: If one of the moderation list files is missing.
            ValueError: If a JSON file is invalid or contains no usable terms.
        """
        self.lexicon_paths = self._build_lexicon_paths(
            french_list_path=french_list_path,
            arabic_list_path=arabic_list_path,
            darija_list_path=darija_list_path,
            lexicon_paths=lexicon_paths,
        )
        self.adult_terms = self._load_adult_terms()
        self.patterns = self._compile_patterns(self.adult_terms)

    def analyze_text(self, text: str) -> dict[str, Any]:
        """Analyze text and detect adult or sexually explicit terms.

        Args:
            text: Scenario text to analyze.

        Returns:
            Dictionary containing adult-content status, risk level, normalized
            score, detected terms, and occurrence count.

        Raises:
            TypeError: If text is not a string.
            RuntimeError: If adult-content analysis fails unexpectedly.
        """
        if not isinstance(text, str):
            logger.error(
                "Invalid text type for adult-content analysis: %s",
                type(text).__name__,
            )
            raise TypeError("text must be a string")

        if not text.strip():
            logger.info("Received empty text for adult-content analysis.")
            return self._build_result(
                adult_content_score=0.0,
                detected_terms=[],
                detected_details=[],
                occurrences_count=0,
                weighted_score=0,
                nudity_matches=[],
            )

        try:
            logger.info("Starting adult-content analysis.")
            normalized_text, index_map = self._normalize_with_map(text)
            detected_terms: list[str] = []
            detected_details: list[dict[str, Any]] = []
            nudity_matches: list[dict[str, Any]] = []
            occurrences_count = 0
            weighted_score = 0

            for term in self.adult_terms:
                pattern = self.patterns[term.normalized_term]
                term_occurrences = 0
                for match in pattern.finditer(normalized_text):
                    start_norm, end_norm = match.span()
                    raw_span = self._map_span(
                        index_map, start_norm, end_norm, len(text)
                    )
                    if raw_span is None:
                        continue
                    raw_start, raw_end = raw_span
                    raw_word = text[raw_start:raw_end].strip() or term.term
                    nudity_matches.append(
                        {
                            "term": term.term,
                            "word": raw_word,
                            "language": term.language,
                            "category": term.category,
                            "snippet": extract_context_snippet(
                                text, raw_start, raw_end
                            ),
                            "start": raw_start,
                            "end": raw_end,
                        }
                    )
                    term_occurrences += 1

                if term_occurrences > 0:
                    detected_terms.append(term.term)
                    occurrences_count += term_occurrences
                    weighted_score += term_occurrences * term.weight
                    detected_details.append(
                        {
                            "term": term.term,
                            "category": term.category,
                            "language": term.language,
                            "weight": term.weight,
                            "occurrences": term_occurrences,
                        }
                    )

            nudity_matches.sort(key=lambda m: m["start"])

            adult_content_score = self._calculate_score(
                weighted_score=weighted_score,
                text=text,
            )

            logger.info(
                "Adult-content analysis completed. occurrences=%s score=%s "
                "matches=%s",
                occurrences_count,
                adult_content_score,
                len(nudity_matches),
            )

            return self._build_result(
                adult_content_score=adult_content_score,
                detected_terms=detected_terms,
                detected_details=detected_details,
                occurrences_count=occurrences_count,
                weighted_score=weighted_score,
                nudity_matches=nudity_matches,
            )
        except Exception as exc:
            logger.exception("Failed to analyze adult content.")
            raise RuntimeError("Failed to analyze adult content") from exc

    def _build_result(
        self,
        adult_content_score: float,
        detected_terms: list[str],
        detected_details: list[dict[str, Any]],
        occurrences_count: int,
        weighted_score: int,
        nudity_matches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a standard adult-content analysis response."""
        result: dict[str, Any] = {
            "contains_adult_content": occurrences_count > 0,
            "risk_level": self._get_risk_level(adult_content_score),
            "adult_content_score": adult_content_score,
            "detected_terms": detected_terms,
            "occurrences_count": occurrences_count,
            "nudity_matches": nudity_matches,
        }
        if occurrences_count > 0:
            result["weighted_score"] = weighted_score
            result["detected_details"] = detected_details

        return result

    @staticmethod
    def _map_span(
        index_map: list[int],
        start_norm: int,
        end_norm: int,
        raw_len: int,
    ) -> tuple[int, int] | None:
        """Map a span in the normalized text back to raw-text indices."""
        if start_norm >= len(index_map):
            return None
        raw_start = index_map[start_norm]
        if end_norm == 0:
            return raw_start, raw_start
        if end_norm <= len(index_map):
            raw_end_idx = end_norm - 1
        else:
            raw_end_idx = len(index_map) - 1
        raw_end = min(raw_len, index_map[raw_end_idx] + 1)
        if raw_end <= raw_start:
            raw_end = min(raw_len, raw_start + 1)
        return raw_start, raw_end

    def _normalize_with_map(self, text: str) -> tuple[str, list[int]]:
        """Normalize text and return (normalized, raw_index_map).

        Mirrors ``_normalize_text`` but keeps a per-character map from the
        normalized output back to the raw input so ``finditer`` spans can
        be translated to raw-text offsets for snippet extraction.
        """
        out_chars: list[str] = []
        idx_map: list[int] = []

        for i, ch in enumerate(text):
            # Drop Arabic diacritics and tatweel.
            if self._ARABIC_DIACRITICS_PATTERN.match(ch):
                continue
            if ch == "ـ":  # tatweel
                continue

            # Apply NFKC to a single char, then casefold.
            nfkc = unicodedata.normalize("NFKC", ch)
            for nch in nfkc:
                if nch in "إأآٱا":  # إأآٱا → ا
                    replacement = "ا"
                elif nch == "ى":  # ى
                    replacement = "ي"
                elif nch == "ؤ":  # ؤ
                    replacement = "و"
                elif nch == "ئ":  # ئ
                    replacement = "ي"
                elif nch == "ة":  # ة
                    replacement = "ه"
                else:
                    replacement = nch
                folded = replacement.casefold()
                if not folded:
                    continue
                for fc in folded:
                    out_chars.append(fc)
                    idx_map.append(i)

        joined = "".join(out_chars)
        # Collapse multiple whitespaces to a single space, mirroring the
        # ``re.sub(r"\\s+", " ", normalized)`` step in ``_normalize_text``.
        # We keep the index map aligned by walking again.
        out2: list[str] = []
        idx2: list[int] = []
        prev_space = False
        for ch, src in zip(joined, idx_map, strict=False):
            if ch.isspace():
                if prev_space:
                    continue
                out2.append(" ")
                idx2.append(src)
                prev_space = True
            else:
                out2.append(ch)
                idx2.append(src)
                prev_space = False
        # Strip leading/trailing whitespace in sync with ``_normalize_text``.
        s, e = 0, len(out2)
        while s < e and out2[s] == " ":
            s += 1
        while e > s and out2[e - 1] == " ":
            e -= 1
        return "".join(out2[s:e]), idx2[s:e]

    def _build_lexicon_paths(
        self,
        french_list_path: str | Path | None,
        arabic_list_path: str | Path | None,
        darija_list_path: str | Path | None,
        lexicon_paths: list[str | Path] | None,
    ) -> list[Path]:
        if lexicon_paths is not None:
            return [Path(path) for path in lexicon_paths]

        if french_list_path is not None or arabic_list_path is not None or darija_list_path is not None:
            paths = []
            if french_list_path is not None:
                paths.append(Path(french_list_path))
            if arabic_list_path is not None:
                paths.append(Path(arabic_list_path))
            if darija_list_path is not None:
                paths.append(Path(darija_list_path))
            return paths

        return [
            Path(french_list_path or self.DEFAULT_FRENCH_LIST_PATH),
            Path(arabic_list_path or self.DEFAULT_ARABIC_LIST_PATH),
            Path(darija_list_path or self.DEFAULT_DARIJA_LIST_PATH),
        ]

    def _load_adult_terms(self) -> list[WeightedTerm]:
        """Load and merge adult-content terms from configured JSON files.

        Returns:
            A sorted list of unique adult-content terms.

        Raises:
            FileNotFoundError: If one of the moderation list files is missing.
            ValueError: If files contain no valid terms.
        """
        terms_by_normalized_value: dict[str, WeightedTerm] = {}

        for file_path in self.lexicon_paths:
            logger.info("Loading adult-content list: %s", file_path)
            for term in self._load_terms_from_json(file_path):
                existing = terms_by_normalized_value.get(term.normalized_term)
                if existing is None or term.weight > existing.weight:
                    terms_by_normalized_value[term.normalized_term] = term

        cleaned_terms = sorted(
            terms_by_normalized_value.values(),
            key=lambda item: item.normalized_term,
        )
        if not cleaned_terms:
            logger.error("No adult-content terms loaded from moderation lists.")
            raise ValueError("adult-content lists must contain at least one term")

        logger.info("Loaded %s adult-content terms.", len(cleaned_terms))
        return cleaned_terms

    def _load_terms_from_json(self, file_path: Path) -> list[WeightedTerm]:
        """Load adult-content terms from one JSON file.

        Args:
            file_path: JSON file path to read.

        Returns:
            List of normalized adult-content terms.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the JSON format is invalid or contains no terms.
        """
        if not file_path.exists():
            logger.error("Adult-content list file not found: %s", file_path)
            raise FileNotFoundError(f"Adult-content list file not found: {file_path}")

        try:
            with file_path.open("r", encoding="utf-8") as json_file:
                data = json.load(json_file)
        except json.JSONDecodeError as exc:
            logger.exception("Invalid adult-content JSON file: %s", file_path)
            raise ValueError(f"Invalid JSON file: {file_path}") from exc

        raw_terms = self._extract_terms(data)
        if not raw_terms:
            raise ValueError(f"Adult-content list contains no valid terms: {file_path}")

        language = self._detect_language_from_path(file_path)
        weighted_terms = []
        for item in raw_terms:
            term = str(item["term"]).strip()
            if not term:
                continue

            weighted_terms.append(
                WeightedTerm(
                    term=term.casefold(),
                    normalized_term=self._normalize_text(term),
                    weight=max(1, min(int(item.get("weight", 3)), 5)),
                    category=str(item.get("category", "uncategorized")),
                    language=language,
                )
            )

        return weighted_terms

    def _extract_terms(self, data: Any) -> list[dict[str, Any]]:
        """Extract terms from supported JSON structures."""
        if isinstance(data, list):
            return [
                {"term": item, "weight": 3, "category": "terms"}
                for item in data
                if isinstance(item, str)
            ]

        if isinstance(data, dict):
            terms: list[dict[str, Any]] = []
            for key, values in data.items():
                values = data.get(key, [])
                if isinstance(values, list):
                    for item in values:
                        if isinstance(item, str):
                            terms.append(
                                {"term": item, "weight": 3, "category": key}
                            )
                        elif isinstance(item, dict) and isinstance(item.get("term"), str):
                            terms.append(
                                {
                                    "term": item["term"],
                                    "weight": item.get("weight", 3),
                                    "category": key,
                                }
                            )
            return terms

        return []

    def _compile_patterns(self, terms: list[WeightedTerm]) -> dict[str, re.Pattern[str]]:
        """Compile regex patterns for all adult-content terms."""
        patterns: dict[str, re.Pattern[str]] = {}

        for term in terms:
            escaped_parts = [re.escape(part) for part in term.normalized_term.split()]
            expression = r"\s+".join(escaped_parts)
            patterns[term.normalized_term] = re.compile(
                rf"(?<![\w\u0640]){expression}(?![\w\u0640])",
                re.IGNORECASE | re.UNICODE,
            )

        return patterns

    def _calculate_score(self, weighted_score: int, text: str) -> float:
        """Calculate a normalized weighted adult-content score between 0 and 100."""
        word_count = len(self._WORD_COUNT_PATTERN.findall(self._normalize_text(text)))
        if word_count == 0:
            return 0.0

        return round(min((weighted_score / word_count) * 100, 100.0), 2)

    def _get_risk_level(self, score: float) -> str:
        """Return the risk level associated with a normalized score."""
        if score <= 20:
            return "low"

        if score <= 60:
            return "medium"

        return "high"

    def _normalize_text(self, text: str) -> str:
        """Normalize French, Arabic and Darija text for resilient matching."""
        normalized = unicodedata.normalize("NFKC", text).casefold()
        normalized = self._ARABIC_DIACRITICS_PATTERN.sub("", normalized)
        normalized = normalized.replace("\u0640", "")
        normalized = re.sub("[إأآٱا]", "ا", normalized)
        normalized = normalized.replace("ى", "ي")
        normalized = normalized.replace("ؤ", "و")
        normalized = normalized.replace("ئ", "ي")
        normalized = normalized.replace("ة", "ه")
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _detect_language_from_path(self, file_path: Path) -> str:
        name = file_path.name.lower()
        if "_ar" in name:
            return "ar"
        if "darija" in name:
            return "darija"
        if "_fr" in name:
            return "fr"
        return "unknown"

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeightedTerm:
    """Moderation lexicon term with a severity weight."""

    term: str
    normalized_term: str
    weight: int
    category: str
    language: str


# Fallback Darija/Arabic profanity list (kept here so the detection still works
# even if the JSON files were misconfigured). Lower-cased forms are detected
# after Arabic normalization, so listing the canonical form is sufficient.
DARIJA_PROFANITY_WORDS: set[str] = {
    "قحاب",
    "زوامل",
    "زامل",
    "9hab",
    "qhab",
    "zamel",
    "zawamel",
    "zwamel",
}


_ARABIC_DIACRITICS_RE = re.compile(
    r"[ؐ-ًؚ-ٰٟۖ-ۭ]"
)


def normalize_arabic_text(text: str) -> str:
    """Normalize Arabic/Darija text for resilient matching.

    Strips diacritics and tatweel, normalizes hamza variants, ta marbouta, and
    alif maksoura. Applies NFKC and case-folding on top so the result also
    works for Latin characters.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _ARABIC_DIACRITICS_RE.sub("", normalized)
    normalized = normalized.replace("ـ", "")  # tatweel
    normalized = re.sub("[إأآٱا]", "ا", normalized)
    normalized = normalized.replace("ى", "ي")  # ى -> ي
    normalized = normalized.replace("ؤ", "و")  # ؤ -> و
    normalized = normalized.replace("ئ", "ي")  # ئ -> ي
    normalized = normalized.replace("ة", "ه")  # ة -> ه
    return normalized


def extract_context_snippet(
    text: str,
    start: int,
    end: int,
    window: int = 80,
) -> str:
    """Return a readable snippet around the detected word.

    Tries to extend the window to the nearest sentence boundaries on each side,
    collapses excessive whitespace, and adds ellipsis markers when the snippet
    does not cover the start or end of the text.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    length = len(text)
    if length == 0 or start < 0 or end <= start:
        return ""

    start = max(0, min(start, length))
    end = max(start, min(end, length))

    boundary_chars = ".!?؟…\n\r"
    snippet_start = max(0, start - window)
    snippet_end = min(length, end + window)

    # Snap left bound to the previous sentence boundary if one is found.
    cut_start = snippet_start
    for i in range(start - 1, snippet_start - 1, -1):
        if text[i] in boundary_chars:
            cut_start = i + 1
            break

    # Snap right bound to the next sentence boundary if one is found.
    cut_end = snippet_end
    for i in range(end, snippet_end):
        if text[i] in boundary_chars:
            cut_end = i + 1
            break

    snippet = text[cut_start:cut_end]
    snippet = re.sub(r"[ \t\f\v]*\n[ \t\f\v]*", " ", snippet)
    snippet = re.sub(r"\s+", " ", snippet).strip()

    prefix = "" if cut_start == 0 else "... "
    suffix = "" if cut_end == length else " ..."
    return f"{prefix}{snippet}{suffix}".strip()


class ProfanityService:
    """Service responsible for detecting profanity in scenario text."""

    DEFAULT_FRENCH_LIST_PATH = Path("data/moderation_lists/vulgarity_fr.json")
    DEFAULT_ARABIC_LIST_PATH = Path("data/moderation_lists/vulgarity_ar.json")
    DEFAULT_DARIJA_LIST_PATH = Path("data/moderation_lists/vulgarity_darija.json")
    _ARABIC_DIACRITICS_PATTERN = _ARABIC_DIACRITICS_RE
    _WORD_COUNT_PATTERN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)

    def __init__(
        self,
        french_list_path: str | Path | None = None,
        arabic_list_path: str | Path | None = None,
        darija_list_path: str | Path | None = None,
        lexicon_paths: list[str | Path] | None = None,
        use_wiqaya: bool = True,
    ) -> None:
        """Initialize the profanity service and load forbidden terms."""
        self.lexicon_paths = self._build_lexicon_paths(
            french_list_path=french_list_path,
            arabic_list_path=arabic_list_path,
            darija_list_path=darija_list_path,
            lexicon_paths=lexicon_paths,
        )
        self.forbidden_terms = self._load_forbidden_terms()
        self._merge_fallback_darija_terms()
        self.patterns = self._compile_patterns(self.forbidden_terms)
        self._wiqaya = None
        self._wiqaya_enabled = use_wiqaya
        if use_wiqaya:
            self._wiqaya = self._try_init_wiqaya()

    def analyze_text(self, text: str) -> dict[str, Any]:
        """Analyze text and detect vulgar words or expressions.

        Returns a dict with backwards-compatible fields
        (``contains_profanity``, ``profanity_score``, ``detected_words``,
        ``occurrences_count``) plus structured matches:

        * ``vulgarity_matches``: list of ``{word, language, category, snippet,
          start, end}`` for each occurrence found in the original text.
        * ``vulgarity_found_words``: deduped list of detected words.
        * ``vulgarity_categories``: deduped list of categories triggered.
        """
        if not isinstance(text, str):
            logger.error(
                "Invalid text type for profanity analysis: %s",
                type(text).__name__,
            )
            raise TypeError("text must be a string")

        if not text.strip():
            logger.info("Received empty text for profanity analysis.")
            return self._empty_result()

        try:
            logger.info("Starting profanity analysis.")
            normalized_text, index_map = self._normalize_with_map(text)

            matches: list[dict[str, Any]] = []
            detected_details: list[dict[str, Any]] = []
            detected_words_order: list[str] = []
            occurrences_count = 0
            weighted_score = 0
            seen_spans: set[tuple[int, int, str]] = set()

            for term in self.forbidden_terms:
                pattern = self.patterns[term.normalized_term]
                term_occurrences = 0
                for match in pattern.finditer(normalized_text):
                    start_norm, end_norm = match.span()
                    raw_span = self._map_span(index_map, start_norm, end_norm, len(text))
                    if raw_span is None:
                        continue
                    raw_start, raw_end = raw_span
                    if not self._valid_arabic_boundary(text, raw_start, raw_end):
                        continue
                    key = (raw_start, raw_end, term.normalized_term)
                    if key in seen_spans:
                        continue
                    seen_spans.add(key)
                    term_occurrences += 1
                    raw_word = text[raw_start:raw_end].strip() or term.term
                    matches.append(
                        {
                            "word": raw_word,
                            "language": term.language,
                            "category": term.category,
                            "snippet": extract_context_snippet(text, raw_start, raw_end),
                            "start": raw_start,
                            "end": raw_end,
                        }
                    )

                if term_occurrences > 0:
                    if term.term not in detected_words_order:
                        detected_words_order.append(term.term)
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

            # Optional reinforcement via wiqaya for Arabic.
            wiqaya_matches = self._scan_with_wiqaya(text, seen_spans)
            for entry in wiqaya_matches:
                matches.append(entry)
                occurrences_count += 1
                weighted_score += 1
                if entry["word"] not in detected_words_order:
                    detected_words_order.append(entry["word"])

            profanity_score = self._calculate_score(weighted_score, text)
            matches.sort(key=lambda m: m["start"])

            found_words = self._unique_preserve_order(m["word"] for m in matches)
            if not found_words:
                found_words = list(detected_words_order)
            categories = sorted({m["category"] for m in matches if m.get("category")})

            logger.info(
                "Profanity analysis completed. occurrences=%s score=%s matches=%s",
                occurrences_count,
                profanity_score,
                len(matches),
            )

            result: dict[str, Any] = {
                "contains_profanity": occurrences_count > 0,
                "profanity_score": profanity_score,
                "detected_words": detected_words_order,
                "occurrences_count": occurrences_count,
                "vulgarity_matches": matches,
                "vulgarity_found_words": found_words,
                "vulgarity_categories": categories,
            }
            if occurrences_count > 0:
                result["weighted_score"] = weighted_score
                result["detected_details"] = detected_details

            return result
        except Exception as exc:
            logger.exception("Failed to analyze profanity.")
            raise RuntimeError("Failed to analyze profanity") from exc

    # ---------- internals ----------

    def _empty_result(self) -> dict[str, Any]:
        return {
            "contains_profanity": False,
            "profanity_score": 0.0,
            "detected_words": [],
            "occurrences_count": 0,
            "vulgarity_matches": [],
            "vulgarity_found_words": [],
            "vulgarity_categories": [],
        }

    _ARABIC_LETTER_RE = re.compile(r"[ء-ي]")
    _ALLOWED_ARABIC_PREFIXES: set[str] = set("وفبلكساأإ")

    def _valid_arabic_boundary(self, text: str, start: int, end: int) -> bool:
        """Reject matches glued to other Arabic letters that are not common prefixes."""
        if start > 0:
            prev = text[start - 1]
            if self._ARABIC_LETTER_RE.match(prev) and prev not in self._ALLOWED_ARABIC_PREFIXES:
                return False
        if end < len(text):
            nxt = text[end]
            if self._ARABIC_LETTER_RE.match(nxt):
                return False
        return True

    @staticmethod
    def _unique_preserve_order(items: Any) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def _map_span(
        self,
        index_map: list[int],
        start_norm: int,
        end_norm: int,
        raw_length: int,
    ) -> tuple[int, int] | None:
        """Map a normalized-text span back to raw-text offsets."""
        if not index_map:
            return None
        if start_norm >= len(index_map):
            return None
        last_norm = max(0, min(end_norm, len(index_map)) - 1)
        if last_norm < start_norm:
            return None
        raw_start = index_map[start_norm]
        raw_end = min(raw_length, index_map[last_norm] + 1)
        if raw_end <= raw_start:
            return None
        return raw_start, raw_end

    def _build_lexicon_paths(
        self,
        french_list_path: str | Path | None,
        arabic_list_path: str | Path | None,
        darija_list_path: str | Path | None,
        lexicon_paths: list[str | Path] | None,
    ) -> list[Path]:
        if lexicon_paths is not None:
            return [Path(path) for path in lexicon_paths]

        if (
            french_list_path is not None
            or arabic_list_path is not None
            or darija_list_path is not None
        ):
            paths = []
            if french_list_path is not None:
                paths.append(Path(french_list_path))
            if arabic_list_path is not None:
                paths.append(Path(arabic_list_path))
            if darija_list_path is not None:
                paths.append(Path(darija_list_path))
            return paths

        return [
            Path(self.DEFAULT_FRENCH_LIST_PATH),
            Path(self.DEFAULT_ARABIC_LIST_PATH),
            Path(self.DEFAULT_DARIJA_LIST_PATH),
        ]

    def _load_forbidden_terms(self) -> list[WeightedTerm]:
        terms_by_normalized_value: dict[str, WeightedTerm] = {}

        for file_path in self.lexicon_paths:
            logger.info("Loading profanity list: %s", file_path)
            for term in self._load_terms_from_json(file_path):
                existing = terms_by_normalized_value.get(term.normalized_term)
                if existing is None or term.weight > existing.weight:
                    terms_by_normalized_value[term.normalized_term] = term

        cleaned_terms = sorted(
            terms_by_normalized_value.values(),
            key=lambda item: item.normalized_term,
        )
        if not cleaned_terms:
            logger.error("No profanity terms loaded from moderation lists.")
            raise ValueError("profanity lists must contain at least one term")

        logger.info("Loaded %s profanity terms.", len(cleaned_terms))
        return cleaned_terms

    def _merge_fallback_darija_terms(self) -> None:
        """Ensure the hardcoded Darija fallback list is always available."""
        index = {term.normalized_term: term for term in self.forbidden_terms}
        for raw in DARIJA_PROFANITY_WORDS:
            normalized = normalize_arabic_text(raw).strip()
            if not normalized or normalized in index:
                continue
            term = WeightedTerm(
                term=raw.casefold(),
                normalized_term=normalized,
                weight=4,
                category="profanity",
                language="ar/darija",
            )
            index[normalized] = term
            self.forbidden_terms.append(term)
        self.forbidden_terms.sort(key=lambda item: item.normalized_term)

    def _load_terms_from_json(self, file_path: Path) -> list[WeightedTerm]:
        if not file_path.exists():
            logger.error("Profanity list file not found: %s", file_path)
            raise FileNotFoundError(f"Profanity list file not found: {file_path}")

        try:
            with file_path.open("r", encoding="utf-8") as json_file:
                data = json.load(json_file)
        except json.JSONDecodeError as exc:
            logger.exception("Invalid profanity JSON file: %s", file_path)
            raise ValueError(f"Invalid JSON file: {file_path}") from exc

        raw_terms = self._extract_terms(data)
        if not raw_terms:
            raise ValueError(f"Profanity list contains no valid terms: {file_path}")

        language = self._detect_language_from_path(file_path)
        weighted_terms = []
        for item in raw_terms:
            term = str(item["term"]).strip()
            if not term:
                continue

            weighted_terms.append(
                WeightedTerm(
                    term=term.casefold(),
                    normalized_term=normalize_arabic_text(term).strip(),
                    weight=max(1, min(int(item.get("weight", 1)), 5)),
                    category=str(item.get("category", "uncategorized")),
                    language=language,
                )
            )

        return weighted_terms

    def _extract_terms(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [
                {"term": item, "weight": 1, "category": "terms"}
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
                                {"term": item, "weight": 1, "category": key}
                            )
                        elif isinstance(item, dict) and isinstance(
                            item.get("term"), str
                        ):
                            terms.append(
                                {
                                    "term": item["term"],
                                    "weight": item.get("weight", 1),
                                    "category": key,
                                }
                            )
            return terms

        return []

    def _compile_patterns(
        self, terms: list[WeightedTerm]
    ) -> dict[str, re.Pattern[str]]:
        patterns: dict[str, re.Pattern[str]] = {}

        for term in terms:
            escaped_parts = [re.escape(part) for part in term.normalized_term.split()]
            expression = r"\s+".join(escaped_parts)
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){expression}(?![A-Za-z0-9_])",
                re.IGNORECASE | re.UNICODE,
            )
            patterns[term.normalized_term] = pattern

        return patterns

    def _calculate_score(self, weighted_score: int, text: str) -> float:
        normalized, _ = self._normalize_with_map(text)
        word_count = len(self._WORD_COUNT_PATTERN.findall(normalized))
        if word_count == 0:
            return 0.0
        return round(min((weighted_score / word_count) * 100, 100.0), 2)

    def _normalize_text(self, text: str) -> str:
        """Legacy normalization used for word-count statistics."""
        normalized = normalize_arabic_text(text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _normalize_with_map(self, text: str) -> tuple[str, list[int]]:
        """Normalize text and return (normalized, raw_index_map).

        ``raw_index_map[i]`` gives the raw-text index of the i-th character in
        the normalized output. Whitespace is preserved (only collapsed via the
        ``\\s+`` token in the compiled patterns) so we keep an exact character
        mapping back to the input.
        """
        out_chars: list[str] = []
        idx_map: list[int] = []

        for i, ch in enumerate(text):
            if _ARABIC_DIACRITICS_RE.match(ch):
                continue
            if ch == "ـ":  # tatweel
                continue

            if ch in "إأآٱا":
                replacement = "ا"
            elif ch == "ى":
                replacement = "ي"
            elif ch == "ؤ":
                replacement = "و"
            elif ch == "ئ":
                replacement = "ي"
            elif ch == "ة":
                replacement = "ه"
            else:
                replacement = ch

            folded = replacement.casefold()
            if not folded:
                continue
            for fc in folded:
                out_chars.append(fc)
                idx_map.append(i)

        return "".join(out_chars), idx_map

    def _detect_language_from_path(self, file_path: Path) -> str:
        name = file_path.name.lower()
        if "_ar" in name:
            return "ar"
        if "darija" in name:
            return "darija"
        if "_fr" in name:
            return "fr"
        return "unknown"

    # ---------- optional wiqaya integration ----------

    def _try_init_wiqaya(self):  # pragma: no cover - depends on optional pkg
        try:
            from wiqaya import Wiqaya  # type: ignore[import-not-found]
        except Exception:
            logger.info("wiqaya not installed; falling back to lexicon-only detection.")
            return None

        try:
            instance = Wiqaya(lang="ar")
            logger.info("wiqaya enabled for Arabic profanity reinforcement.")
            return instance
        except Exception as exc:
            logger.warning("wiqaya initialization failed: %s", exc)
            return None

    def _scan_with_wiqaya(
        self,
        text: str,
        seen_spans: set[tuple[int, int, str]],
    ) -> list[dict[str, Any]]:
        if self._wiqaya is None:
            return []

        try:
            detected_words = self._call_wiqaya(text)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("wiqaya scan failed: %s", exc)
            return []

        if not detected_words:
            return []

        added: list[dict[str, Any]] = []
        normalized_text, index_map = self._normalize_with_map(text)
        for word in detected_words:
            if not isinstance(word, str) or not word.strip():
                continue
            term_normalized = normalize_arabic_text(word).strip()
            if not term_normalized:
                continue
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(term_normalized)}(?![A-Za-z0-9_])",
                re.IGNORECASE | re.UNICODE,
            )
            for match in pattern.finditer(normalized_text):
                raw_span = self._map_span(
                    index_map, match.start(), match.end(), len(text)
                )
                if raw_span is None:
                    continue
                raw_start, raw_end = raw_span
                if not self._valid_arabic_boundary(text, raw_start, raw_end):
                    continue
                key = (raw_start, raw_end, term_normalized)
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                raw_word = text[raw_start:raw_end].strip() or word
                added.append(
                    {
                        "word": raw_word,
                        "language": "ar",
                        "category": "wiqaya",
                        "snippet": extract_context_snippet(text, raw_start, raw_end),
                        "start": raw_start,
                        "end": raw_end,
                    }
                )
        return added

    def _call_wiqaya(self, text: str) -> list[str]:  # pragma: no cover
        """Best-effort call into the wiqaya API (interface varies by version)."""
        wiqaya = self._wiqaya
        for method_name in ("detect", "find", "scan", "predict", "analyze"):
            method = getattr(wiqaya, method_name, None)
            if callable(method):
                output = method(text)
                return self._normalize_wiqaya_output(output)
        # Last-resort: treat the instance itself as callable.
        if callable(wiqaya):
            return self._normalize_wiqaya_output(wiqaya(text))
        return []

    @staticmethod
    def _normalize_wiqaya_output(output: Any) -> list[str]:  # pragma: no cover
        if output is None:
            return []
        if isinstance(output, str):
            return [output]
        if isinstance(output, dict):
            for key in ("words", "matches", "tokens", "detected"):
                if isinstance(output.get(key), list):
                    return [str(item) for item in output[key] if item]
            return []
        if isinstance(output, (list, tuple, set)):
            words: list[str] = []
            for item in output:
                if isinstance(item, str):
                    words.append(item)
                elif isinstance(item, dict):
                    word = item.get("word") or item.get("term") or item.get("token")
                    if isinstance(word, str):
                        words.append(word)
            return words
        return []

"""Helpers to produce *display* snippets centred on the actual overlap between
two related passages of text.

These helpers are pure display logic: they are never used to compute scores or
make detection decisions. They only choose *which slice* of the source text is
shown to the user when a plagiarism match has been detected upstream.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


# Minimum number of consecutive matching tokens before we trust the overlap
# enough to centre the snippet on it. Below this threshold we fall back to the
# raw source text — better to show "something readable" than a one-word
# pseudo-match.
MIN_OVERLAP_WORDS = 4

# Tokens we consider too common to count as a real overlap signal — used both
# to strip leading/trailing fluff and as a list of "uninformative" headers we
# don't want a snippet to start with.
LOW_INFORMATION_TOKENS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "have", "are",
        "was", "were", "you", "your", "our", "their", "they", "them", "but",
        "not", "all", "any", "can", "will", "has", "had", "out", "one", "two",
        "page", "chapter", "section", "title", "header", "footer",
        "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "à",
        "au", "aux", "en", "dans", "pour", "sur", "par", "avec", "sans",
        "que", "qui", "quoi", "dont", "où", "est", "sont", "été", "être",
        "avoir", "il", "elle", "ils", "elles", "nous", "vous", "ne", "pas",
        "page", "chapitre", "section", "titre",
        "test", "ligne", "texte", "non", "commun", "remplissage",
    }
)


_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _normalize_token(token: str) -> str:
    """Lowercase + strip diacritics so 'détection' matches 'detection'."""
    folded = unicodedata.normalize("NFKD", token).casefold()
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _tokenize(text: str) -> list[tuple[int, int, str, str]]:
    """Return [(start, end, original_token, normalized_token), ...]."""
    out: list[tuple[int, int, str, str]] = []
    for match in _WORD_RE.finditer(text or ""):
        original = match.group(0)
        out.append(
            (match.start(), match.end(), original, _normalize_token(original))
        )
    return out


def collect_boilerplate_ngrams(
    chunks: list[str],
    n: int = 2,
    min_doc_ratio: float = 0.4,
) -> set[str]:
    """Return n-grams that look like document-wide boilerplate.

    A bigram (default n=2) is considered boilerplate when it appears in at
    least ``min_doc_ratio`` of the supplied chunks. The chunks should come
    from a single document, so phrases inserted in every page (templated
    headers, repeated wrappers) are caught.

    The function is intentionally tolerant: with fewer than 3 chunks it
    returns an empty set, because we don't have enough signal to call
    anything "boilerplate".
    """
    if not chunks or len(chunks) < 3 or n < 1:
        return set()
    threshold = max(2, int(len(chunks) * min_doc_ratio))
    counter: dict[str, int] = {}
    for chunk in chunks:
        if not isinstance(chunk, str):
            continue
        tokens = _tokenize(chunk)
        if len(tokens) < n:
            continue
        norm = [t[3] for t in tokens]
        seen: set[str] = set()
        for i in range(len(norm) - n + 1):
            ngram = " ".join(norm[i : i + n])
            if ngram in seen:
                continue
            seen.add(ngram)
            counter[ngram] = counter.get(ngram, 0) + 1
    return {ngram for ngram, count in counter.items() if count >= threshold}


def _maximal_common_runs(
    norm_a: list[str],
    norm_b: list[str],
    min_length: int,
) -> list[tuple[int, int, int, int]]:
    """Return all maximal common contiguous token runs of length >= min_length.

    Each entry is ``(start_a, end_a, start_b, end_b)`` (half-open).
    """
    runs: list[tuple[int, int, int, int]] = []
    if not norm_a or not norm_b:
        return runs

    len_a, len_b = len(norm_a), len(norm_b)
    previous = [0] * (len_b + 1)
    for i in range(1, len_a + 1):
        current = [0] * (len_b + 1)
        a_token = norm_a[i - 1]
        for j in range(1, len_b + 1):
            if a_token == norm_b[j - 1]:
                current[j] = previous[j - 1] + 1
        # Detect "ends of runs" on the previous diagonal: any cell on the
        # diagonal that exceeds min_length and is not extended by the next
        # one is a maximal run ending at (i-1, j-1).
        for j in range(1, len_b + 1):
            length = previous[j]
            if length < min_length:
                continue
            # The current row at column j+1 tells us whether the diagonal
            # continues. previous comes from row i-1, current is row i.
            continues_diagonal = (
                j + 1 <= len_b and current[j + 1] == previous[j] + 1
            )
            if not continues_diagonal:
                start_a = (i - 1) - length
                start_b = j - length
                runs.append((start_a, i - 1, start_b, j))
        previous = current
    # Tail: collect runs ending on the last row.
    for j in range(1, len_b + 1):
        length = previous[j]
        if length >= min_length:
            start_a = len_a - length
            start_b = j - length
            runs.append((start_a, len_a, start_b, j))
    # Deduplicate identical runs.
    return list({run for run in runs})


def _score_run(
    run: tuple[int, int, int, int],
    norm_b: list[str],
    boilerplate_ngrams: set[str] | None,
) -> float:
    """Score a candidate run by length × informativeness.

    Runs whose bigrams are mostly boilerplate are demoted but never zeroed
    so we still return *something* when only boilerplate is shared.
    """
    _, _, start_b, end_b = run
    length = end_b - start_b
    if not boilerplate_ngrams or length < 2:
        return float(length)
    tokens = norm_b[start_b:end_b]
    bigrams = [(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)]
    if not bigrams:
        return float(length)
    boilerplate_count = sum(
        1 for left, right in bigrams if f"{left} {right}" in boilerplate_ngrams
    )
    informative_ratio = 1.0 - (boilerplate_count / len(bigrams))
    # Floor at 0.15 so an all-boilerplate run still beats no-match-at-all.
    weight = 0.15 + 0.85 * informative_ratio
    return length * weight


def _refine_span_to_non_boilerplate_subrun(
    run: tuple[int, int, int, int],
    norm_b: list[str],
    boilerplate_ngrams: set[str] | None,
) -> tuple[int, int, int, int]:
    """Tighten a run to its longest non-boilerplate sub-run, when possible.

    When the largest matching run covers both a templated wrapper and a
    shorter planted passage (contiguous in both texts), the LCS DP can't
    split them. This refinement walks the bigram mask of the run and keeps
    only the longest consecutive stretch of non-boilerplate bigrams.

    If boilerplate is not provided or the whole run is boilerplate, the
    original run is returned unchanged.
    """
    if not boilerplate_ngrams:
        return run

    start_a, end_a, start_b, end_b = run
    length = end_b - start_b
    if length < MIN_OVERLAP_WORDS + 1:
        return run

    bigram_mask: list[bool] = []  # True = boilerplate
    for i in range(start_b, end_b - 1):
        ngram = f"{norm_b[i]} {norm_b[i + 1]}"
        bigram_mask.append(ngram in boilerplate_ngrams)

    if not any(bigram_mask) or all(bigram_mask):
        return run

    # Longest run of False in the mask.
    best_len = 0
    best_start = 0
    current_len = 0
    current_start = 0
    for i, is_boilerplate in enumerate(bigram_mask):
        if is_boilerplate:
            current_len = 0
            continue
        if current_len == 0:
            current_start = i
        current_len += 1
        if current_len > best_len:
            best_len = current_len
            best_start = current_start

    # A run of N consecutive non-boilerplate bigrams covers N+1 tokens.
    sub_token_count = best_len + 1
    if sub_token_count < MIN_OVERLAP_WORDS:
        return run

    delta_start = best_start
    delta_end = (length - 1) - (best_start + best_len)
    refined = (
        start_a + delta_start,
        end_a - delta_end,
        start_b + delta_start,
        end_b - delta_end,
    )
    return refined


def _trim_low_information_edges(
    run: tuple[int, int, int, int],
    tokens_a: list[tuple[int, int, str, str]],
) -> tuple[int, int, int, int] | None:
    start_a, end_a, start_b, end_b = run
    while end_a - start_a > MIN_OVERLAP_WORDS and tokens_a[start_a][3] in LOW_INFORMATION_TOKENS:
        start_a += 1
        start_b += 1
    while end_a - start_a > MIN_OVERLAP_WORDS and tokens_a[end_a - 1][3] in LOW_INFORMATION_TOKENS:
        end_a -= 1
        end_b -= 1
    if end_a - start_a < MIN_OVERLAP_WORDS:
        return None
    return start_a, end_a, start_b, end_b


def _best_common_token_span(
    tokens_a: list[tuple[int, int, str, str]],
    tokens_b: list[tuple[int, int, str, str]],
    boilerplate_ngrams: set[str] | None = None,
) -> tuple[int, int, int, int] | None:
    """Pick the most informative common token span, anti-boilerplate aware.

    Falls back to the longest common run when no boilerplate hint is given,
    which preserves the original ``_longest_common_token_span`` behaviour.
    """
    if not tokens_a or not tokens_b:
        return None

    norm_a = [t[3] for t in tokens_a]
    norm_b = [t[3] for t in tokens_b]

    runs = _maximal_common_runs(norm_a, norm_b, MIN_OVERLAP_WORDS)
    if not runs:
        return None

    scored = [
        (_score_run(run, norm_b, boilerplate_ngrams), run[1] - run[0], run)
        for run in runs
    ]
    # Sort by score desc, then length desc, then earliest position for stability.
    scored.sort(key=lambda item: (-item[0], -item[1], item[2][0]))
    for _, _, candidate in scored:
        refined = _refine_span_to_non_boilerplate_subrun(
            candidate, norm_b, boilerplate_ngrams
        )
        trimmed = _trim_low_information_edges(refined, tokens_a)
        if trimmed is not None:
            return trimmed
    return None


# Backwards-compatible alias — older imports keep working.
_longest_common_token_span = _best_common_token_span


def _window_around_span(
    text: str,
    char_start: int,
    char_end: int,
    max_chars: int,
    min_chars: int = 0,
) -> tuple[str, int, int]:
    """Return a snippet centred on [char_start, char_end].

    Sentence-boundary snapping is applied so the snippet reads as a complete
    phrase rather than chopped mid-word. When the snapped result is shorter
    than ``min_chars`` and the surrounding text has more content available,
    the window is expanded symmetrically until it reaches ``min_chars``
    (capped at ``max_chars``). This keeps the centred passage visible while
    surfacing a full sentence around it.
    """
    if max_chars <= 0:
        return "", char_start, char_end

    span_length = max(0, char_end - char_start)
    if span_length > max_chars:
        # Overlap itself is longer than the budget — keep its middle so we
        # neither show only the (often boilerplate) beginning nor truncate the
        # end. Sentence-snapping below may shrink it further.
        midpoint = (char_start + char_end) // 2
        half = max_chars // 2
        raw_start = max(0, midpoint - half)
        raw_end = min(len(text), midpoint + (max_chars - half))
    else:
        extra = max(0, max_chars - span_length)
        half = extra // 2
        raw_start = max(0, char_start - half)
        raw_end = min(len(text), char_end + (extra - half))

    boundary_chars = ".!?؟…\n\r"
    snap_start = raw_start
    for i in range(char_start - 1, raw_start - 1, -1):
        if text[i] in boundary_chars:
            snap_start = i + 1
            break

    snap_end = raw_end
    for i in range(char_end, raw_end):
        if text[i] in boundary_chars:
            snap_end = i + 1
            break

    snippet = text[snap_start:snap_end]
    snippet = _WHITESPACE_RE.sub(" ", snippet).strip()

    # If sentence-snapping clipped the snippet too tight, expand the window
    # symmetrically around the original span up to ``min_chars`` (still
    # capped by ``max_chars``). This avoids cutting just after the planted
    # passage's period, e.g. "… Passage identique 2. …" → expand to include
    # the surrounding sentence on either side.
    effective_min = min(min_chars, max_chars, len(text))
    if effective_min > 0 and len(snippet) < effective_min and (
        snap_start > 0 or snap_end < len(text)
    ):
        target = effective_min
        midpoint = (char_start + char_end) // 2
        half = target // 2
        snap_start = max(0, midpoint - half)
        snap_end = min(len(text), midpoint + (target - half))
        snippet = text[snap_start:snap_end]
        snippet = _WHITESPACE_RE.sub(" ", snippet).strip()

    # Final hard cap in case sentence-snapping expanded the window beyond
    # the budget (e.g. a single very long sentence).
    if len(snippet) > max_chars:
        midpoint = len(snippet) // 2
        half = max_chars // 2
        cut_start = max(0, midpoint - half)
        cut_end = min(len(snippet), midpoint + (max_chars - half))
        snippet = snippet[cut_start:cut_end].strip()

    # Add ellipsis markers when we cut content off either side.
    if snap_start > 0:
        snippet = f"… {snippet}"
    if snap_end < len(text):
        snippet = f"{snippet} …"
    return snippet, snap_start, snap_end


def _fallback_snippet(text: str, max_chars: int) -> str:
    if not text:
        return ""
    compact = _WHITESPACE_RE.sub(" ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars].rstrip() + "…"


def build_plagiarism_snippet(
    current_text: str,
    source_text: str,
    fallback_text: str | None = None,
    max_chars: int = 900,
    min_chars: int = 400,
    source_boilerplate_ngrams: set[str] | None = None,
) -> dict[str, Any]:
    """Build a display snippet centred on the real overlap between two passages.

    Args:
        current_text: The current scenario's chunk (or any text from the
            document being analysed). Used to *locate* the overlap.
        source_text: The accent-preserving, display-ready version of the
            matched source chunk. The returned snippet is always carved from
            this text so accents/casing are preserved.
        fallback_text: Optional alternative text used if ``source_text`` is
            empty. Defaults to ``source_text``.
        max_chars: Maximum length of the returned snippet.

    Returns:
        ``{"snippet": str, "snippet_source": "overlap"|"fallback",
        "overlap_text": str|None}``.
        ``snippet`` is always a non-empty string when *any* input text is
        provided.
    """
    source_display = source_text or fallback_text or ""
    fallback_display = fallback_text if fallback_text is not None else source_display

    if not source_display and not current_text:
        return {
            "snippet": "",
            "snippet_source": "fallback",
            "overlap_text": None,
        }

    tokens_current = _tokenize(current_text or "")
    tokens_source = _tokenize(source_display)

    span = _best_common_token_span(
        tokens_current,
        tokens_source,
        boilerplate_ngrams=source_boilerplate_ngrams,
    )
    if span is None or not tokens_source:
        return {
            "snippet": _fallback_snippet(fallback_display or source_display, max_chars),
            "snippet_source": "fallback",
            "overlap_text": None,
        }

    _, _, start_b, end_b = span
    overlap_start_char = tokens_source[start_b][0]
    overlap_end_char = tokens_source[end_b - 1][1]
    overlap_text = source_display[overlap_start_char:overlap_end_char]

    snippet, _, _ = _window_around_span(
        source_display,
        overlap_start_char,
        overlap_end_char,
        max_chars,
        min_chars=min_chars,
    )

    if not snippet:
        snippet = _fallback_snippet(fallback_display or source_display, max_chars)
        return {
            "snippet": snippet,
            "snippet_source": "fallback",
            "overlap_text": overlap_text or None,
        }

    return {
        "snippet": snippet,
        "snippet_source": "overlap",
        "overlap_text": overlap_text or None,
    }

"""Composite plagiarism scoring.

A high cosine similarity between two scenario chunks is not sufficient to call
a passage plagiarism. Screenplays share a lot of generic vocabulary
(``INT/EXT``, ``JOUR/NUIT``, ``rue``, ``voiture``, ``regarde``, ``sourire``…)
that pushes embeddings close together even when no actual text was copied.

This module computes a **composite** plagiarism score that combines:

* the semantic (embedding) score returned by Qdrant,
* a lexical Jaccard score on informative tokens,
* an exact n-gram overlap score (windows of 5–8 informative tokens),
* a dialogue-overlap score on quoted/colon-prefixed lines,

then applies a series of penalties when the signal looks generic. A simple
heuristic also flags likely false positives so the UI can demote them.

This module is pure (no IO, no global state) and is safe to import from any
service. It does not change the embedding pipeline, the API contract, or the
stored documents — callers are free to attach the new fields next to existing
ones.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Scenario-specific stopwords
# ---------------------------------------------------------------------------

# Tokens that appear in nearly every screenplay and inflate lexical similarity
# without indicating real overlap. The list is intentionally conservative —
# only words that the user explicitly listed plus a few obvious variants
# (singular/plural, accented/unaccented). They are matched after diacritics
# folding, so the canonical entry is the unaccented lowercase form.
SCENARIO_STOPWORDS: frozenset[str] = frozenset(
    {
        # Slug-line tokens
        "int", "ext", "intext",
        "jour", "jours", "nuit", "nuits",
        "matin", "matins", "soir", "soirs",
        "apres", "midi", "apres-midi", "apresmidi",
        "cont", "continue", "continuite", "suite",
        # Locations
        "rue", "rues", "maison", "maisons", "appartement",
        "voiture", "voitures", "moto", "motos", "route", "routes",
        "ville", "villes", "porte", "portes", "fenetre", "fenetres",
        "chambre", "salon", "cuisine", "bureau", "couloir",
        # Characters (generic)
        "homme", "hommes", "femme", "femmes", "fille", "filles",
        "garcon", "garcons", "enfant", "enfants", "pere", "peres",
        "mere", "meres", "chauffeur", "chauffeurs",
        # Actions / verbs (generic stage directions)
        "regarde", "regardent", "regard", "regards",
        "sourit", "sourient", "sourire", "sourires",
        "leve", "levee", "lever",
        "entre", "entrent", "sort", "sortent",
        "marche", "marchent", "arrive", "arrivent",
        "part", "partent", "assis", "assise", "debout",
        # Direction terms
        "silence", "pause", "temps", "musique", "voix",
        "scene", "scenes", "plan", "plans",
        # Body parts
        "main", "mains", "oeil", "yeux", "tete", "tetes", "visage",
        # Common props
        "telephone", "telephones",
        # Common French/English fluff already used elsewhere
        "the", "and", "for", "with", "that", "this",
        "une", "des", "les", "pour", "dans", "avec", "cette",
        "ligne", "texte", "page", "non", "commun", "remplissage",
        "confidentiel", "confidentielle", "document",
        "scenario", "copyright", "entete", "footer", "header",
    }
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Arabic letters live in the U+0600–U+06FF block (and a few extensions). We
# keep them whole even when shorter than 3 characters because Arabic words can
# legitimately be short.
_ARABIC_RE = re.compile(r"[؀-ۿ]+")


def _strip_diacritics(token: str) -> str:
    folded = unicodedata.normalize("NFKD", token).casefold()
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _is_arabic(token: str) -> bool:
    return bool(_ARABIC_RE.search(token))


def normalize_tokens(text: str) -> list[str]:
    """Return informative tokens from ``text``.

    Lowercases, strips diacritics, removes scenario stopwords, drops tokens
    shorter than 3 characters unless they contain Arabic letters.
    """
    if not text:
        return []
    tokens: list[str] = []
    for match in _WORD_RE.finditer(text):
        raw = match.group(0)
        is_arabic = _is_arabic(raw)
        normalized = _strip_diacritics(raw)
        if not normalized:
            continue
        if normalized in SCENARIO_STOPWORDS:
            continue
        if not is_arabic and len(normalized) < 3:
            continue
        tokens.append(normalized)
    return tokens


# ---------------------------------------------------------------------------
# Lexical similarity components
# ---------------------------------------------------------------------------


def jaccard_score(tokens_a: list[str], tokens_b: list[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def ngram_overlap_score(
    tokens_a: list[str],
    tokens_b: list[str],
    min_n: int = 5,
    max_n: int = 8,
) -> float:
    """Proportion of n-grams (5..8) from the shorter side found in the other.

    Returns 0.0 when either side is too short to form an n-gram of length
    ``min_n``. The score is the maximum hit-rate across the n-gram sizes,
    so a single long shared phrase strongly dominates.
    """
    if not tokens_a or not tokens_b:
        return 0.0
    best = 0.0
    for n in range(min_n, max_n + 1):
        if len(tokens_a) < n or len(tokens_b) < n:
            continue
        grams_a = {tuple(tokens_a[i : i + n]) for i in range(len(tokens_a) - n + 1)}
        grams_b = {tuple(tokens_b[i : i + n]) for i in range(len(tokens_b) - n + 1)}
        if not grams_a or not grams_b:
            continue
        smaller = min(len(grams_a), len(grams_b))
        hits = len(grams_a & grams_b)
        rate = hits / smaller if smaller else 0.0
        if rate > best:
            best = rate
    return best


# ---------------------------------------------------------------------------
# Named entity / character overlap
# ---------------------------------------------------------------------------

# A "character / named entity" candidate is a token that:
#   * appears with a leading uppercase letter (Latin script), or
#   * is fully uppercase and at least 3 letters long (screenplay style),
# and is not in the scenario stopword list once folded.
_CAPITAL_RE = re.compile(r"\b([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ\-']+|[A-Z]{3,})\b")


def extract_named_entities(text: str) -> set[str]:
    if not text:
        return set()
    found: set[str] = set()
    for match in _CAPITAL_RE.finditer(text):
        token = match.group(0)
        normalized = _strip_diacritics(token)
        if normalized in SCENARIO_STOPWORDS:
            continue
        if len(normalized) < 3:
            continue
        found.add(normalized)
    return found


def named_entity_overlap_score(text_a: str, text_b: str) -> float:
    entities_a = extract_named_entities(text_a)
    entities_b = extract_named_entities(text_b)
    if not entities_a or not entities_b:
        return 0.0
    common = entities_a & entities_b
    smaller = min(len(entities_a), len(entities_b))
    return len(common) / smaller if smaller else 0.0


# ---------------------------------------------------------------------------
# Dialogue overlap
# ---------------------------------------------------------------------------

_DIALOGUE_LINE_RE = re.compile(
    r"^\s*(?:[A-ZÀ-ÖØ-Þ' \-]{2,}\s*[:\-]|[—–-])\s*(.+)$",
    re.MULTILINE,
)


def extract_dialogue_lines(text: str) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for match in _DIALOGUE_LINE_RE.finditer(text):
        content = match.group(1).strip()
        if content:
            lines.append(content)
    return lines


def dialogue_overlap_score(text_a: str, text_b: str) -> float:
    lines_a = extract_dialogue_lines(text_a)
    lines_b = extract_dialogue_lines(text_b)
    if not lines_a or not lines_b:
        return 0.0
    tokens_a = normalize_tokens(" ".join(lines_a))
    tokens_b = normalize_tokens(" ".join(lines_b))
    if not tokens_a or not tokens_b:
        return 0.0
    return max(
        jaccard_score(tokens_a, tokens_b),
        ngram_overlap_score(tokens_a, tokens_b, min_n=4, max_n=6),
    )


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------


def compute_composite_scores(
    *,
    semantic_score: float,
    query_text: str,
    source_text: str,
) -> dict[str, float]:
    """Compute the composite plagiarism score and its components.

    ``semantic_score`` is expected in [0, 1] — typically the cosine similarity
    returned by Qdrant. ``query_text`` and ``source_text`` are the *display*
    versions of the chunks (accents preserved) so that named-entity detection
    works correctly.
    """
    semantic = max(0.0, min(1.0, float(semantic_score or 0.0)))
    tokens_q = normalize_tokens(query_text)
    tokens_s = normalize_tokens(source_text)

    lexical = jaccard_score(tokens_q, tokens_s)
    exact_overlap = ngram_overlap_score(tokens_q, tokens_s)
    entity_overlap = named_entity_overlap_score(query_text, source_text)
    dialogue = dialogue_overlap_score(query_text, source_text)

    final = (
        0.45 * semantic
        + 0.25 * lexical
        + 0.20 * exact_overlap
        + 0.10 * dialogue
    )

    # ----- Penalties (caps) -----
    if lexical < 0.10 and exact_overlap < 0.05:
        final = min(final, 0.25)
    elif lexical < 0.15 and exact_overlap < 0.08:
        final = min(final, 0.35)

    # No meaningful shared informative vocabulary.
    common_significant = len(set(tokens_q) & set(tokens_s))
    if common_significant <= 1:
        final = min(final, 0.20)

    # Character names totally disjoint and weak lexical signal.
    if entity_overlap == 0.0 and lexical < 0.20:
        final = min(final, 0.40)

    # Match driven almost exclusively by generic vocabulary: the informative
    # tokens are few and the n-gram overlap is negligible.
    informative_min = min(len(set(tokens_q)), len(set(tokens_s)))
    if informative_min <= 6 and exact_overlap < 0.05:
        final = min(final, 0.30)

    # No shared 5+token run AND no shared named entity / character. Even if
    # the Jaccard score looks decent, the shared vocabulary is almost
    # certainly generic register noise (verbs like "fait", pronouns, etc.)
    # rather than evidence of copying.
    if exact_overlap < 0.05 and entity_overlap == 0.0:
        final = min(final, 0.30)

    final = max(0.0, min(1.0, final))

    return {
        "semantic_score": round(semantic, 4),
        "lexical_score": round(lexical, 4),
        "exact_overlap_score": round(exact_overlap, 4),
        "named_entity_overlap_score": round(entity_overlap, 4),
        "dialogue_overlap_score": round(dialogue, 4),
        "final_score": round(final, 4),
        "common_significant_tokens": common_significant,
    }


# ---------------------------------------------------------------------------
# False positive detection
# ---------------------------------------------------------------------------


def is_likely_false_positive(scores: dict[str, float]) -> tuple[bool, str | None]:
    """Return ``(is_fp, reason)``.

    The match is considered a likely false positive when:
      * semantic_score is high but lexical_score is very low,
      * exact_overlap_score is very low,
      * no dialogue overlap,
      * no shared named entity / character.
    """
    semantic = float(scores.get("semantic_score", 0.0) or 0.0)
    lexical = float(scores.get("lexical_score", 0.0) or 0.0)
    exact = float(scores.get("exact_overlap_score", 0.0) or 0.0)
    dialogue = float(scores.get("dialogue_overlap_score", 0.0) or 0.0)
    entities = float(scores.get("named_entity_overlap_score", 0.0) or 0.0)

    if (
        semantic >= 0.70
        and lexical < 0.15
        and exact < 0.05
        and dialogue == 0.0
        and entities == 0.0
    ):
        return True, (
            "Faux positif probable : similarité sémantique générique sans "
            "chevauchement textuel significatif."
        )
    return False, None


# ---------------------------------------------------------------------------
# Risk thresholds
# ---------------------------------------------------------------------------

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_VERY_HIGH = "very_high"


def risk_from_composite(scores: dict[str, float]) -> str:
    """Map a composite-score dict to a risk bucket.

    HIGH / VERY_HIGH require *some* real lexical overlap. Without it, the
    match is capped at MEDIUM (or LOW for purely generic content).
    """
    final = float(scores.get("final_score", 0.0) or 0.0)
    lexical = float(scores.get("lexical_score", 0.0) or 0.0)
    exact = float(scores.get("exact_overlap_score", 0.0) or 0.0)

    # Hard cap: a HIGH or VERY_HIGH bucket requires lexical OR exact-overlap
    # evidence that real text is shared.
    real_overlap = lexical >= 0.20 or exact >= 0.10

    if final >= 0.75 and real_overlap:
        return RISK_VERY_HIGH
    if final >= 0.55 and real_overlap:
        return RISK_HIGH
    if final >= 0.30:
        return RISK_MEDIUM
    return RISK_LOW


# ---------------------------------------------------------------------------
# Percent formatting
# ---------------------------------------------------------------------------


def format_percent(value: Any) -> int:
    """Return ``value`` formatted as an integer percentage in [0, 100].

    Accepts both ratios (0..1) and percentages (0..100). Values up to 1.0
    (inclusive) are treated as ratios — everything beyond is treated as an
    already-scaled percentage. ``None`` / unparseable values yield 0.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number != number:  # NaN
        return 0
    if number <= 1.0:
        number *= 100.0
    rounded = int(round(number))
    if rounded < 0:
        return 0
    if rounded > 100:
        return 100
    return rounded

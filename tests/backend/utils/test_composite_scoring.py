"""Tests for the composite plagiarism scoring layer."""

from backend.utils.composite_scoring import (
    compute_composite_scores,
    extract_dialogue_lines,
    extract_named_entities,
    format_percent,
    is_likely_false_positive,
    jaccard_score,
    ngram_overlap_score,
    normalize_tokens,
    risk_from_composite,
)


# ---------------------------------------------------------------------------
# normalize_tokens
# ---------------------------------------------------------------------------


def test_normalize_tokens_strips_scenario_stopwords() -> None:
    text = "INT. RUE — JOUR. Un homme regarde la voiture."
    tokens = normalize_tokens(text)
    # All stopwords (int, rue, jour, homme, regarde, voiture) must be filtered.
    assert tokens == []


def test_normalize_tokens_keeps_informative_words() -> None:
    text = "Sarah découvre le manuscrit dans la chambre forte."
    tokens = normalize_tokens(text)
    assert "sarah" in tokens
    assert "decouvre" in tokens
    assert "manuscrit" in tokens
    assert "forte" in tokens
    # ``dans`` and ``la`` are too short / stopwords.
    assert "dans" not in tokens
    assert "la" not in tokens


def test_normalize_tokens_keeps_short_arabic_tokens() -> None:
    text = "في الشارع تجلس امرأة"
    tokens = normalize_tokens(text)
    # Each Arabic word should be preserved even if it has 2 characters.
    assert any("في" in t or t == "في" for t in tokens)


# ---------------------------------------------------------------------------
# Lexical components
# ---------------------------------------------------------------------------


def test_jaccard_score_on_disjoint_vocabulary_is_zero() -> None:
    a = ["sarah", "manuscrit", "tour"]
    b = ["karim", "ordinateur", "plage"]
    assert jaccard_score(a, b) == 0.0


def test_ngram_overlap_score_detects_shared_run() -> None:
    a = "Sarah découvre le manuscrit caché derrière la bibliothèque oubliée".split()
    b = "Karim disait que Sarah découvre le manuscrit caché derrière la bibliothèque".split()
    score = ngram_overlap_score(a, b, min_n=5, max_n=8)
    assert score > 0.0


def test_ngram_overlap_score_zero_when_no_shared_run() -> None:
    a = "le chien dort sur le tapis du salon".split()
    b = "la pluie tombe sur les toits de la ville".split()
    assert ngram_overlap_score(a, b, min_n=5, max_n=8) == 0.0


# ---------------------------------------------------------------------------
# Named entities / dialogue
# ---------------------------------------------------------------------------


def test_extract_named_entities_finds_uppercase_names() -> None:
    text = "ROSITA Pourquoi dis-tu que c'est lui ? VICTORIA Rosita, franchement."
    entities = extract_named_entities(text)
    assert "rosita" in entities
    assert "victoria" in entities


def test_extract_dialogue_lines_collects_speaker_lines() -> None:
    text = (
        "ROSITA : Je veux aller travailler.\n"
        "VICTORIA : Tu n'as pas à subir ça.\n"
        "— Allez vas-y.\n"
    )
    lines = extract_dialogue_lines(text)
    assert any("travailler" in line for line in lines)
    assert any("subir" in line for line in lines)


# ---------------------------------------------------------------------------
# Composite score — false-positive case (generic vocabulary)
# ---------------------------------------------------------------------------


def test_composite_caps_generic_match_voiture_chauffeur() -> None:
    # Both texts mention voiture / chauffeur / rue but with totally different
    # vocabulary. Even with a high semantic score, the composite must remain
    # low and risk must be LOW.
    text_a = (
        "INT. RUE - JOUR. Un homme entre dans la voiture. "
        "Le chauffeur démarre lentement vers la ville."
    )
    text_b = (
        "EXT. RUE - NUIT. Une femme regarde la voiture passer. "
        "Le chauffeur klaxonne devant la maison."
    )
    scores = compute_composite_scores(
        semantic_score=0.83,
        query_text=text_a,
        source_text=text_b,
    )
    assert scores["final_score"] <= 0.30
    assert risk_from_composite(scores) == "low"


def test_composite_caps_generic_clin_oeil_match() -> None:
    text_a = (
        "Fredi lui fait un clin d'œil. Victoria rougit. "
        "Pablo les observe tous les deux."
    )
    text_b = (
        "Le lourdaud lui fait un clin d'œil. Rim recule, agressive. "
        "Aïssa entre dans le champ."
    )
    scores = compute_composite_scores(
        semantic_score=0.80,
        query_text=text_a,
        source_text=text_b,
    )
    assert scores["final_score"] <= 0.30


# ---------------------------------------------------------------------------
# Composite score — true plagiarism cases
# ---------------------------------------------------------------------------


def test_composite_rewards_near_verbatim_copy() -> None:
    # Almost identical text with one minor edit — must reach VERY_HIGH.
    text_a = (
        "Sarah découvre le manuscrit caché derrière la bibliothèque oubliée. "
        "Elle lit chaque ligne avec une fascination mêlée d'effroi, et le "
        "secret du tailleur du roi commence à se révéler à elle."
    )
    text_b = (
        "Sarah découvre le manuscrit caché derrière la bibliothèque oubliée. "
        "Elle lit chaque ligne avec une fascination mêlée d'effroi, et le "
        "secret du tailleur du roi commence enfin à se révéler à elle."
    )
    scores = compute_composite_scores(
        semantic_score=0.96,
        query_text=text_a,
        source_text=text_b,
    )
    assert scores["final_score"] >= 0.75
    assert risk_from_composite(scores) in ("high", "very_high")


def test_composite_rewards_heavy_paraphrase_with_shared_vocabulary() -> None:
    text_a = (
        "Sarah lit le manuscrit du tailleur dans la bibliothèque oubliée. "
        "Le secret du roi se révèle à mesure qu'elle tourne les pages."
    )
    text_b = (
        "Dans la bibliothèque oubliée, Sarah parcourt le manuscrit du tailleur. "
        "Page après page, le secret du roi se révèle à elle."
    )
    scores = compute_composite_scores(
        semantic_score=0.88,
        query_text=text_a,
        source_text=text_b,
    )
    assert scores["final_score"] >= 0.55
    assert risk_from_composite(scores) in ("high", "very_high")


# ---------------------------------------------------------------------------
# False-positive flagging
# ---------------------------------------------------------------------------


def test_is_likely_false_positive_flags_generic_match() -> None:
    scores = {
        "semantic_score": 0.85,
        "lexical_score": 0.05,
        "exact_overlap_score": 0.0,
        "dialogue_overlap_score": 0.0,
        "named_entity_overlap_score": 0.0,
    }
    flagged, reason = is_likely_false_positive(scores)
    assert flagged is True
    assert reason is not None
    assert "faux positif" in reason.lower()


def test_is_likely_false_positive_passes_real_match() -> None:
    scores = {
        "semantic_score": 0.92,
        "lexical_score": 0.45,
        "exact_overlap_score": 0.30,
        "dialogue_overlap_score": 0.20,
        "named_entity_overlap_score": 0.50,
    }
    flagged, _ = is_likely_false_positive(scores)
    assert flagged is False


# ---------------------------------------------------------------------------
# Risk gate
# ---------------------------------------------------------------------------


def test_risk_caps_at_medium_without_real_overlap() -> None:
    # High composite but no lexical/exact evidence → cannot be HIGH/VERY_HIGH.
    scores = {
        "final_score": 0.80,
        "lexical_score": 0.10,
        "exact_overlap_score": 0.05,
    }
    assert risk_from_composite(scores) == "medium"


def test_risk_high_requires_lexical_or_exact_signal() -> None:
    scores = {
        "final_score": 0.60,
        "lexical_score": 0.30,
        "exact_overlap_score": 0.15,
    }
    assert risk_from_composite(scores) == "high"


# ---------------------------------------------------------------------------
# format_percent
# ---------------------------------------------------------------------------


def test_format_percent_rounds_to_integer() -> None:
    assert format_percent(0.0) == 0
    assert format_percent(0.4451) == 45
    assert format_percent(0.8507) == 85
    assert format_percent(1.0) == 100


def test_format_percent_accepts_already_scaled_value() -> None:
    assert format_percent(85.07) == 85
    assert format_percent(44.51) == 45
    assert format_percent(12.0) == 12


def test_format_percent_handles_garbage_input() -> None:
    assert format_percent(None) == 0
    assert format_percent("nope") == 0
    assert format_percent(-5) == 0
    assert format_percent(150) == 100


def test_format_percent_never_contains_decimals() -> None:
    for value in (0.0, 0.123, 0.4451, 0.8507, 1.0, 12.0, 50, 100):
        result = format_percent(value)
        assert isinstance(result, int)
        assert "." not in str(result)

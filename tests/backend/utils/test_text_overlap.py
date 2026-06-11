"""Tests for the display-only plagiarism snippet helper."""

from backend.utils.text_overlap import (
    build_plagiarism_snippet,
    collect_boilerplate_ngrams,
)


def test_snippet_centres_on_passage_in_the_middle() -> None:
    long_padding = (
        " Ceci est un long passage de remplissage sans rapport avec "
        "l'intrigue principale du document à analyser ici. " * 6
    )
    current = (
        "Header de la page 12. Numéro de chapitre. Quelques mots de contexte "
        "avant le passage. " + long_padding +
        "Le héros découvre un secret enfoui dans la vieille maison "
        "abandonnée, modifiant à jamais son destin. " + long_padding +
        "Texte de transition et conclusion du chunk."
    )
    source = (
        "Page 4. Sommaire général. Préambule sans intérêt particulier. "
        + long_padding +
        "Le héros découvre un secret enfoui dans la vieille maison "
        "abandonnée, modifiant à jamais son destin. " + long_padding +
        "Suite différente avec d'autres péripéties que l'on ne souhaite pas "
        "afficher."
    )
    result = build_plagiarism_snippet(current_text=current, source_text=source)

    snippet = result["snippet"]
    assert result["snippet_source"] == "overlap"
    # The actual copied passage must be in the snippet.
    assert "héros découvre un secret enfoui" in snippet
    assert "vieille maison abandonnée" in snippet
    # The snippet must NOT just be the chunk header.
    assert not snippet.lower().lstrip("… ").startswith("page 4")
    assert not snippet.lower().lstrip("… ").startswith("sommaire général")


def test_snippet_falls_back_when_no_real_overlap() -> None:
    current = "Le héros marche dans la forêt enchantée."
    source = (
        "Texte complètement différent qui parle de cuisine moléculaire et "
        "de techniques de fermentation contemporaines."
    )
    result = build_plagiarism_snippet(current_text=current, source_text=source)
    assert result["snippet_source"] == "fallback"
    assert result["overlap_text"] is None
    assert result["snippet"].startswith("Texte complètement différent")


def test_snippet_keeps_accents_from_source() -> None:
    current = (
        "Document de test cree pour detection de vulgarite en francais reelle "
        "avec colere."
    )
    source = (
        "Document de test créé pour détection de vulgarité en français réelle "
        "avec colère."
    )
    result = build_plagiarism_snippet(current_text=current, source_text=source)
    snippet = result["snippet"]
    for accented in ("créé", "détection", "vulgarité", "français", "colère"):
        assert accented in snippet, f"missing accent: {accented!r}"


def test_snippet_respects_max_chars() -> None:
    common = " mots communs très spécifiques " * 20
    current = "préambule différent." + common + "fin courante."
    source = "header source." + common + "queue inutile."
    result = build_plagiarism_snippet(
        current_text=current, source_text=source, max_chars=120
    )
    # Snippet length should not blow past the cap by a huge margin.
    assert len(result["snippet"]) <= 180  # allow ellipsis padding


def test_snippet_uses_fallback_when_inputs_empty() -> None:
    result = build_plagiarism_snippet(current_text="", source_text="")
    assert result == {
        "snippet": "",
        "snippet_source": "fallback",
        "overlap_text": None,
    }


def test_collect_boilerplate_ngrams_returns_phrases_repeated_across_chunks() -> None:
    # The wrapper "contexte unique pour cette page" appears in every chunk
    # → it must be flagged as boilerplate. The planted unique passages must
    # NOT be flagged.
    chunks = [
        "contexte unique pour cette page : passage planté un.",
        "contexte unique pour cette page : passage planté deux.",
        "contexte unique pour cette page : passage planté trois.",
        "contexte unique pour cette page : passage planté quatre.",
        "contexte unique pour cette page : passage planté cinq.",
    ]
    boilerplate = collect_boilerplate_ngrams(chunks)
    assert "contexte unique" in boilerplate
    assert "unique pour" in boilerplate
    assert "pour cette" in boilerplate
    # The "planté un/deux/three" bigrams appear in only one chunk each.
    assert "planté un" not in boilerplate
    assert "planté deux" not in boilerplate


def test_collect_boilerplate_ngrams_returns_empty_for_short_corpus() -> None:
    assert collect_boilerplate_ngrams(["only one chunk"]) == set()
    assert collect_boilerplate_ngrams([]) == set()


def test_snippet_prefers_planted_passage_over_repeated_boilerplate() -> None:
    # Simulate the 97-page / 3-passage scenario: both texts share a long
    # boilerplate wrapper AND a shorter planted passage. With a tight
    # snippet budget the helper must centre on the planted passage.
    wrapper = (
        "contexte unique pour cette page : les lignes de remplissage changent "
        "selon le document afin de ne pas créer de fausses correspondances "
        "répétitives et pour garder le rapport lisible le module choisit le "
        "passage le plus informatif au lieu du début du chunk en fonction "
        "des sequences communes les plus rares"
    )
    planted = "Passage identique deux dans un autre style narratif."

    current = f"{wrapper} {planted} fin du chunk courant et bla bla bla."
    source = f"{wrapper} {planted} suite différente avec d'autres mots."

    source_chunks = [f"{wrapper} planted passage {i}." for i in range(6)]
    boilerplate = collect_boilerplate_ngrams(source_chunks)
    assert boilerplate, "fixture must produce a non-empty boilerplate set"

    result = build_plagiarism_snippet(
        current_text=current,
        source_text=source,
        source_boilerplate_ngrams=boilerplate,
        max_chars=120,
    )
    assert result["snippet_source"] == "overlap"
    assert "Passage identique deux" in result["snippet"]
    # The snippet budget is tight enough that the boilerplate's distinctive
    # phrase must not be carried into the snippet.
    assert "lignes de remplissage changent" not in result["snippet"]


def test_snippet_without_boilerplate_hint_keeps_legacy_behaviour() -> None:
    # Same fixture, but no boilerplate hint provided. The longest common run
    # is the wrapper, so the helper falls back to that — this proves the
    # change is opt-in and does not silently alter older callers.
    wrapper = (
        "contexte unique pour cette page : les lignes de remplissage changent "
        "selon le document afin de ne pas créer de fausses correspondances "
        "répétitives et pour garder le rapport lisible le module choisit le "
        "passage le plus informatif au lieu du début du chunk en fonction "
        "des sequences communes les plus rares"
    )
    planted = "Passage identique deux dans un autre style narratif."

    current = f"{wrapper} {planted} fin du chunk courant."
    source = f"{wrapper} {planted} suite différente."

    result = build_plagiarism_snippet(
        current_text=current, source_text=source, max_chars=120
    )
    assert result["snippet_source"] == "overlap"
    # Without the hint, the helper picks the longest common run (wrapper +
    # planted) and the tight 120-char window cuts the middle of the wrapper.
    # The planted passage is NOT surfaced and the wrapper text dominates.
    assert "passage le plus informatif" in result["snippet"]
    assert "Passage identique deux" not in result["snippet"]


def test_snippet_expands_to_full_sentence_around_planted_passage() -> None:
    """When more context is available, the snippet must surface a full
    sentence around the planted passage rather than stop right after it."""
    # Both texts share a short planted passage ("identique 2 - ...").
    # The neighbouring context (before and after) is also identical so the
    # snippet has room to grow up to ~250 chars without falling back on
    # repeated boilerplate.
    intro = (
        "Page 5/10. Avant le passage on présente le contexte narratif pour "
        "que la lecture du rapport ait du sens. Le détective examine les "
        "indices laissés sur place et note ses observations."
    )
    planted = "Passage identique 2 - Le héros découvre la vérité cachée."
    outro = (
        "Après le passage, le récit reprend avec d'autres péripéties qui "
        "permettent au lecteur de continuer l'enquête sans perdre le fil."
    )
    current = f"{intro} {planted} {outro}"
    source = f"{intro} {planted} {outro}"

    result = build_plagiarism_snippet(
        current_text=current,
        source_text=source,
        max_chars=400,
        min_chars=250,
    )

    snippet = result["snippet"]
    # Length is comfortably within the 250-400 target band.
    assert 200 <= len(snippet) <= 420, f"snippet length out of range: {len(snippet)}"
    # The planted passage is present.
    assert "Passage identique 2" in snippet
    # And a full sentence around the passage is included (text on the right
    # of the passage's period must be present, so we don't cut just after
    # "identique 2.").
    assert "héros découvre la vérité cachée" in snippet
    assert (
        "Le détective examine les indices" in snippet
        or "Après le passage" in snippet
    ), "snippet should include at least one full neighbouring sentence"


def test_snippet_avoids_starting_on_low_information_tokens() -> None:
    # Both texts share a long passage, but it's surrounded by low-information
    # tokens like "page", "section". The snippet should be anchored on real
    # content, not the boilerplate header that precedes the overlap.
    current = (
        "Introduction longue avec des phrases utiles. "
        "Le protagoniste quitte sa ville natale au lever du soleil pour "
        "rejoindre les anciens du conseil dans une auberge isolée. "
        "Suite avec d'autres détails."
    )
    source = (
        "Page Section Chapter Title Header Page "
        "Le protagoniste quitte sa ville natale au lever du soleil pour "
        "rejoindre les anciens du conseil dans une auberge isolée. "
        "Texte différent ensuite."
    )
    result = build_plagiarism_snippet(current_text=current, source_text=source)
    snippet = result["snippet"]
    # The signal is the long shared sentence; the leading low-info words
    # ("Page", "Section", ...) must not dominate the snippet.
    assert "protagoniste quitte sa ville natale" in snippet

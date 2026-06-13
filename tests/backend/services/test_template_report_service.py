import pytest

from backend.services.template_report_service import TemplateReportService


def _empty_profanity() -> dict:
    return {
        "contains_profanity": False,
        "profanity_score": 0.0,
        "detected_words": [],
        "occurrences_count": 0,
        "vulgarity_matches": [],
    }


def _empty_adult() -> dict:
    return {
        "contains_adult_content": False,
        "risk_level": "low",
        "adult_content_score": 0.0,
        "detected_terms": [],
        "occurrences_count": 0,
    }


# ---------- Core behavior ----------


def test_generate_report_returns_low_risk_when_nothing_is_detected() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.0,
            "plagiarism_detected": False,
            "matches": [],
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={
            "original_filename": "demo.pdf",
            "file_name": "stored.pdf",
            "words_count": 1200,
            "chunks_count": 3,
            "raw_characters_count": 7000,
            "cleaned_characters_count": 6800,
        },
    )

    assert result["scenario_id"] == "scenario-1"
    assert result["risk_level"] == "low"
    assert "ne présente pas de risque" in result["summary"]
    assert "Aucun passage similaire significatif" in result["plagiarism_explanation"]
    assert result["risk_justification"]
    assert result["conclusion"]


def test_generate_report_mentions_similar_passages() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.55,
            "plagiarism_detected": True,
            "matches": [
                {
                    "filename": "other.pdf",
                    "stored_filename": "other.pdf",
                    "original_filename": "Histoire originale.pdf",
                    "chunk_index": 0,
                    "chunk_text": "passage original",
                    "matched_scenario_id": "scenario-2",
                    "matched_chunk_id": "scenario-2_0",
                    "matched_chunk_text": "passage similaire",
                    "similarity_score": 0.82,
                }
            ],
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={"chunks_count": 1},
    )

    assert result["risk_level"] == "medium"
    assert "scenario-2" in result["plagiarism_explanation"]
    assert "passage similaire" in result["generated_report"]
    assert any(
        "Vérifier manuellement les passages similaires" in rec
        for rec in result["recommendations"]
    )


def test_generate_report_returns_high_risk_for_high_similarity() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.89,
            "plagiarism_detected": True,
            "matches": [],
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    assert result["risk_level"] == "high"
    assert "risque important" in result["summary"]
    assert any("similarité très élevé" in rec for rec in result["recommendations"])


def test_generate_report_explains_moderation_findings() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.1,
            "plagiarism_detected": False,
            "matches": [],
        },
        profanity_result={
            "contains_profanity": True,
            "profanity_score": 25.0,
            "detected_words": ["insulte"],
            "vulgarity_matches": [],
        },
        adult_content_result={
            "contains_adult_content": True,
            "risk_level": "medium",
            "adult_content_score": 42.5,
            "detected_terms": ["contenu sensible"],
        },
        document_stats={"pages_count": 4},
    )

    assert result["risk_level"] == "medium"
    moderation = result["moderation_explanation"]
    assert "Score de vulgarité : 25.00 / 100" in moderation
    assert "Score contenu adulte : 42.50 / 100" in moderation
    assert "insulte" in result["generated_report"]
    assert "contenu sensible" in result["generated_report"]


def test_generate_report_raises_value_error_for_empty_scenario_id() -> None:
    service = TemplateReportService()

    with pytest.raises(ValueError, match="scenario_id must not be empty"):
        service.generate_report(" ", {}, {}, {}, {})


def test_generate_report_raises_type_error_for_invalid_result_input() -> None:
    service = TemplateReportService()

    with pytest.raises(TypeError, match="plagiarism_result must be a dictionary"):
        service.generate_report(
            "scenario-1",
            [],  # type: ignore[arg-type]
            {},
            {},
            {},
        )


# ---------- Renderer structure ----------


def test_generated_report_has_canonical_sections() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={"words_count": 100, "chunks_count": 2},
    )

    report = result["generated_report"]
    for section in (
        "Rapport d'analyse",
        "Niveau de risque",
        "Statistiques du document",
        "Analyse plagiat",
        "Analyse modération",
        "Recommandations",
        "Conclusion",
    ):
        assert section in report, f"section missing: {section}"


def test_generated_report_keeps_french_accents() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={"words_count": 100, "chunks_count": 1},
    )

    report = result["generated_report"]
    for word in ("Résumé", "similarité", "été", "Recommandations", "détecté"):
        assert word in report, f"missing accented token: {word!r}"


def test_generated_report_renders_document_stats_as_list_not_dict() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={
            "original_filename": "demo.pdf",
            "file_name": "stored_demo.pdf",
            "words_count": 1500,
            "chunks_count": 5,
            "raw_characters_count": 8000,
            "cleaned_characters_count": 7900,
        },
    )

    report = result["generated_report"]
    assert "{'" not in report
    assert "- Nom du fichier original : demo.pdf" in report
    assert "- Nom stocké : stored_demo.pdf" in report
    assert "- Nombre de mots : 1500" in report


# ---------- Plagiarism block ----------


def test_plagiarism_block_truncates_extract_and_hides_none_scenario() -> None:
    service = TemplateReportService()
    long_text = "A" * 2000

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.5,
            "matches": [
                {
                    "filename": "other.pdf",
                    "stored_filename": "other.pdf",
                    "matched_scenario_id": None,
                    "matched_chunk_text": long_text,
                    "similarity_score": 0.6,
                }
            ],
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    block = result["plagiarism_explanation"]
    assert "scenario None" not in result["generated_report"]
    assert "Scénario source : None" not in block
    # No leak via dictionary repr.
    assert "None" not in block.split("Extrait")[0]
    assert "A" * 2000 not in block
    assert "..." in block
    assert "Nom stocké : other.pdf" in block
    assert "Scénario source : non disponible" in block
    assert "Nom original : non disponible" in block


def test_plagiarism_block_shows_original_and_stored_filenames() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.79,
            "matches": [
                {
                    "filename": "3275a7bf.pdf",
                    "stored_filename": "3275a7bf.pdf",
                    "original_filename": "test_langage_grossier_fr_darija.pdf",
                    "matched_scenario_id": "ba6b004c-7c4d-4e60-8ea3-d01e14b39a65",
                    "matched_chunk_text": "extrait du chunk similaire",
                    "similarity_score": 0.7922,
                }
            ],
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    block = result["plagiarism_explanation"]
    assert "Nom original : test_langage_grossier_fr_darija.pdf" in block
    assert "Nom stocké : 3275a7bf.pdf" in block
    assert "Scénario source : ba6b004c-7c4d-4e60-8ea3-d01e14b39a65" in block
    assert "Score : 79.22%" in block


def test_plagiarism_block_supports_display_text_for_accents() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.5,
            "matches": [
                {
                    "filename": "other.pdf",
                    "matched_chunk_text": "extrait sans accents creee",
                    "matched_chunk_text_display": "extrait avec accents créée",
                    "similarity_score": 0.5,
                }
            ],
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    assert "créée" in result["plagiarism_explanation"]


# ---------- Vulgarity grouping & translation ----------


def test_vulgarity_matches_are_rendered_in_report_with_translated_categories() -> None:
    service = TemplateReportService()

    vulgarity_matches = [
        {
            "word": "قحاب",
            "language": "darija",
            "category": "profanity",
            "snippet": "... فيه قحاب زوامل زامل داخل فقرة قصيرة ...",
        },
        {
            "word": "زوامل",
            "language": "darija",
            "category": "profanity",
            "snippet": "... فيه قحاب زوامل زامل داخل فقرة قصيرة ...",
        },
        {
            "word": "merde",
            "language": "fr",
            "category": "offensive_words",
            "snippet": "... un mot comme merde dans la phrase ...",
        },
    ]

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result={
            "contains_profanity": True,
            "profanity_score": 10.0,
            "detected_words": ["قحاب", "زوامل", "merde"],
            "vulgarity_matches": vulgarity_matches,
        },
        adult_content_result=_empty_adult(),
        document_stats={"words_count": 10},
    )

    report = result["generated_report"]
    assert "Passages contenant des mots vulgaires" in report
    # French categories
    assert "Catégorie : vulgarité" in report  # translated profanity
    assert "Catégorie : mots offensants" in report  # translated offensive_words
    # All snippets and words present
    for match in vulgarity_matches:
        assert match["snippet"] in report
        assert match["word"] in report


def test_vulgarity_summary_groups_case_insensitive_words() -> None:
    service = TemplateReportService()

    vulgarity_matches = [
        {"word": "Putain", "language": "fr", "category": "offensive_words",
         "snippet": "Putain ici"},
        {"word": "putain", "language": "fr", "category": "offensive_words",
         "snippet": "putain encore"},
        {"word": "PUTAIN", "language": "fr", "category": "offensive_words",
         "snippet": "PUTAIN cri"},
        {"word": "قحاب", "language": "darija", "category": "profanity",
         "snippet": "... قحاب ..."},
        {"word": "زوامل", "language": "darija", "category": "profanity",
         "snippet": "... زوامل ..."},
        {"word": "زامل", "language": "darija", "category": "profanity",
         "snippet": "... زامل ..."},
    ]

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result={
            "contains_profanity": True,
            "profanity_score": 8.5,
            "detected_words": ["Putain", "putain", "قحاب", "زوامل", "زامل"],
            "vulgarity_matches": vulgarity_matches,
        },
        adult_content_result=_empty_adult(),
        document_stats={"words_count": 20},
    )

    report = result["generated_report"]
    moderation = result["moderation_explanation"]

    # Score unit visible.
    assert "Score de vulgarité : 8.50 / 100" in moderation

    # Grouped summary present.
    assert "Résumé des mots détectés" in moderation
    # Putain/putain/PUTAIN merged into a single bullet with 3 occurrences.
    assert (
        "- putain : 3 occurrence(s), langue français, catégorie mots offensants"
        in moderation
    )
    # Each darija word listed once.
    for word in ("قحاب", "زوامل", "زامل"):
        assert (
            f"- {word} : 1 occurrence(s), langue darija, catégorie vulgarité"
            in moderation
        )

    # Detail section still shows original case for each occurrence.
    detail = moderation.split("Passages contenant des mots vulgaires")[-1]
    assert "Mot détecté : Putain" in detail
    assert "Mot détecté : putain" in detail
    assert "Mot détecté : PUTAIN" in detail

    # Headline word list is deduped case-insensitively.
    headline_words = report.split("Mots détectés :")[1].split(".")[0]
    assert headline_words.lower().count("putain") == 1


def test_vulgarity_summary_handles_unknown_category() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result={
            "contains_profanity": True,
            "profanity_score": 5.0,
            "detected_words": ["foo"],
            "vulgarity_matches": [
                {
                    "word": "foo",
                    "language": "fr",
                    "category": "weird_cat",
                    "snippet": "... foo ...",
                }
            ],
        },
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    assert "non classé (weird_cat)" in result["moderation_explanation"]


# ---------- Risk justification & conclusion ----------


def test_summary_contains_risk_justification() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={"global_similarity_score": 0.79, "matches": []},
        profanity_result={
            "contains_profanity": True,
            "profanity_score": 8.5,
            "detected_words": ["putain"],
            "vulgarity_matches": [
                {"word": "putain", "language": "fr", "category": "offensive_words",
                 "snippet": "..."},
                {"word": "قحاب", "language": "darija", "category": "profanity",
                 "snippet": "..."},
            ],
        },
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    summary = result["summary"]
    assert "Le niveau ÉLEVÉ est principalement justifié par" in summary
    # Primary driver is the similarity (the one that crossed the HIGH threshold).
    assert "score de similarité élevé de" in summary
    # Vulgarity is mentioned as a secondary signal, not as a justifier.
    assert "signal secondaire à vérifier" in summary
    assert "termes vulgaires" in summary
    # Languages rendered in long form.
    assert "français" in summary and "darija" in summary
    # Never claim that a faible score is the main reason for HIGH.
    assert "score de vulgarité faible" not in summary


def test_summary_low_risk_mentions_no_signal() -> None:
    service = TemplateReportService()
    result = service.generate_report(
        scenario_id="s1",
        plagiarism_result={"global_similarity_score": 0.0, "matches": []},
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={},
    )
    assert "ne présente pas de risque significatif" in result["summary"]


def test_conclusion_section_present_with_correct_tone() -> None:
    service = TemplateReportService()

    high = service.generate_report(
        "s1",
        {"global_similarity_score": 0.85, "matches": []},
        {"contains_profanity": True, "profanity_score": 8.5,
         "detected_words": ["putain"],
         "vulgarity_matches": [
             {"word": "putain", "language": "fr", "category": "offensive_words",
              "snippet": "..."},
             {"word": "قحاب", "language": "darija", "category": "profanity",
              "snippet": "..."},
         ]},
        _empty_adult(),
        {},
    )
    assert "Conclusion" in high["generated_report"]
    assert "doit être revu manuellement" in high["conclusion"]
    assert "ÉLEVÉ" in high["conclusion"]

    medium = service.generate_report(
        "s1",
        {"global_similarity_score": 0.5, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    assert "signaux modérés" in medium["conclusion"]

    low = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    assert "ne présente pas de risque significatif" in low["conclusion"]


def test_recommendations_are_contextual() -> None:
    service = TemplateReportService()

    high = service.generate_report(
        "s1",
        {"global_similarity_score": 0.85, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    assert any("très élevé" in r for r in high["recommendations"])

    vulg = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        {
            "contains_profanity": True,
            "profanity_score": 5.0,
            "detected_words": ["merde"],
            "vulgarity_matches": [],
        },
        _empty_adult(),
        {},
    )
    assert any("vulgaires" in r for r in vulg["recommendations"])


# ---------- New v3 polish tests ----------


def test_high_risk_justification_separates_primary_and_secondary() -> None:
    """HIGH from similarity must not be 'justified' by a faible vulgarity score."""
    service = TemplateReportService()
    result = service.generate_report(
        "scenario-x",
        {"global_similarity_score": 0.79, "matches": []},
        {
            "contains_profanity": True,
            "profanity_score": 8.5,
            "occurrences_count": 4,
            "detected_words": ["merde"],
            "vulgarity_matches": [
                {"word": "merde", "language": "fr",
                 "category": "offensive_words", "snippet": "..."},
            ],
        },
        _empty_adult(),
        {},
    )

    summary = result["summary"]
    assert "score de similarité élevé de 79.00%" in summary
    assert "signal secondaire" in summary
    # The faible-score wording must not be used as a justifier.
    assert "vulgarité faible" not in summary
    # No bare language code leaks.
    assert " fr " not in summary and "langue fr." not in summary


def test_high_risk_justification_with_only_similarity_has_no_secondary() -> None:
    service = TemplateReportService()
    result = service.generate_report(
        "scenario-x",
        {"global_similarity_score": 0.85, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    summary = result["summary"]
    assert "score de similarité élevé de 85.00%" in summary
    assert "signal secondaire" not in summary


def test_low_risk_justification_has_no_signal_phrase() -> None:
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    assert "Aucun signal fort" in result["summary"]


def test_languages_are_rendered_in_long_form() -> None:
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        {
            "contains_profanity": True,
            "profanity_score": 5.0,
            "occurrences_count": 3,
            "detected_words": ["merde", "fuck", "خايب"],
            "vulgarity_matches": [
                {"word": "merde", "language": "fr",
                 "category": "offensive_words", "snippet": "... merde ..."},
                {"word": "fuck", "language": "en",
                 "category": "offensive_words", "snippet": "... fuck ..."},
                {"word": "خايب", "language": "ar",
                 "category": "insults", "snippet": "... خايب ..."},
            ],
        },
        _empty_adult(),
        {},
    )
    moderation = result["moderation_explanation"]
    assert "langue français" in moderation
    assert "langue anglais" in moderation
    assert "langue arabe" in moderation
    # Short codes must not appear as a standalone language label.
    assert "langue fr," not in moderation
    assert "langue en," not in moderation
    assert "langue ar," not in moderation


def test_adult_score_line_always_present_even_when_no_signal() -> None:
    service = TemplateReportService()

    # No adult content
    result = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    moderation = result["moderation_explanation"]
    assert "Score contenu adulte : 0.00 / 100" in moderation
    assert "Aucun contenu adulte significatif" in moderation

    # With adult content
    with_adult = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        _empty_profanity(),
        {
            "contains_adult_content": True,
            "adult_content_score": 35.0,
            "risk_level": "medium",
            "detected_terms": ["sensible"],
        },
        {},
    )
    moderation2 = with_adult["moderation_explanation"]
    assert "Score contenu adulte : 35.00 / 100" in moderation2
    assert "Contenu adulte significatif détecté" in moderation2


def test_recommendations_are_precise_and_include_traceability() -> None:
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {
            "global_similarity_score": 0.85,
            "matches": [{"filename": "x.pdf", "similarity_score": 0.85,
                         "matched_chunk_text": "extrait"}],
        },
        {
            "contains_profanity": True,
            "profanity_score": 12.5,
            "occurrences_count": 7,
            "detected_words": ["merde"],
            "vulgarity_matches": [
                {"word": "merde", "language": "fr",
                 "category": "offensive_words", "snippet": "..."}
            ],
        },
        {
            "contains_adult_content": True,
            "adult_content_score": 30.0,
            "risk_level": "medium",
            "detected_terms": ["x"],
        },
        {},
    )
    recommendations = result["recommendations"]
    joined = " | ".join(recommendations)

    # Similarity recommendation mentions the actual score.
    assert "85.00%" in joined
    # Vulgarity recommendation mentions occurrences and score.
    assert "7 occurrence(s)" in joined
    assert "12.50 / 100" in joined
    # Adult content recommendation mentions its score.
    assert "30.00 / 100" in joined
    # Traceability instruction is always present, as the last item.
    assert recommendations[-1] == (
        "Conserver une trace de la décision finale dans l'historique de l'analyse."
    )


def test_traceability_recommendation_added_even_when_no_risk() -> None:
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {"global_similarity_score": 0.0, "matches": []},
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    assert any(
        "Conserver une trace de la décision finale" in rec
        for rec in result["recommendations"]
    )


def test_plagiarism_extract_prefers_accented_display_field() -> None:
    """Extract source priority must keep accented version when available."""
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {
            "global_similarity_score": 0.5,
            "matches": [
                {
                    "filename": "x.pdf",
                    "matched_chunk_text": "cree detection vulgarite francais",
                    "matched_chunk_text_display": "créé détection vulgarité français",
                    "similarity_score": 0.5,
                }
            ],
        },
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    assert "créé détection vulgarité français" in result["plagiarism_explanation"]


def test_plagiarism_extract_prefers_overlap_text_over_chunk_start() -> None:
    """If a match exposes overlap_text, the report must show it, not the chunk start."""
    service = TemplateReportService()
    chunk_start = (
        "Page 1. Header de chapitre. Texte d'introduction non pertinent qui "
        "remplit le début du chunk avec du contexte non copié."
    )
    overlap = (
        "Le héros découvre un secret enfoui dans la vieille maison abandonnée."
    )
    result = service.generate_report(
        "s1",
        {
            "global_similarity_score": 0.82,
            "matches": [
                {
                    "filename": "x.pdf",
                    "matched_chunk_text": f"{chunk_start} {overlap}",
                    "matched_chunk_text_display": f"{chunk_start} {overlap}",
                    "overlap_text": overlap,
                    "similarity_score": 0.82,
                }
            ],
        },
        _empty_profanity(),
        _empty_adult(),
        {},
    )

    block = result["plagiarism_explanation"]
    assert overlap in block
    # The chunk start (header / introduction) must NOT leak into the extract.
    assert "Header de chapitre" not in block
    assert "Texte d'introduction non pertinent" not in block


def test_plagiarism_extract_prefers_polished_snippet_over_raw_overlap() -> None:
    """`snippet` (centred + expanded by build_plagiarism_snippet) must win
    over the raw `overlap_text` so the report shows the 250-400 char passage,
    not the tight token-level match."""
    service = TemplateReportService()
    polished = (
        "Contexte avant le passage. Véritable passage similaire entre les "
        "deux documents avec assez de phrase pour être lisible. Contexte "
        "après le passage qui apporte la continuité narrative attendue."
    )
    result = service.generate_report(
        "s1",
        {
            "global_similarity_score": 0.72,
            "matches": [
                {
                    "filename": "x.pdf",
                    "snippet": polished,
                    "overlap_text": "passage similaire entre les deux",
                    "similarity_score": 0.72,
                }
            ],
        },
        _empty_profanity(),
        _empty_adult(),
        {},
    )

    block = result["plagiarism_explanation"]
    # The polished snippet wins and gives the full context.
    assert "Contexte avant le passage" in block
    assert "Véritable passage similaire entre les deux documents" in block
    assert "Contexte après le passage" in block


def test_plagiarism_extract_falls_back_to_overlap_text_when_no_snippet() -> None:
    """When a match lacks `snippet`, `overlap_text` is still preferred."""
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {
            "global_similarity_score": 0.6,
            "matches": [
                {
                    "filename": "x.pdf",
                    "overlap_text": "passage extrait du run commun",
                    "matched_chunk_text": "header inutile ... passage extrait du run commun ... fin",
                    "similarity_score": 0.6,
                }
            ],
        },
        _empty_profanity(),
        _empty_adult(),
        {},
    )
    block = result["plagiarism_explanation"]
    assert "passage extrait du run commun" in block
    # `matched_chunk_text` (with leading noise) should not win over overlap_text.
    assert "header inutile" not in block


def test_plagiarism_extract_uses_display_field_with_accents() -> None:
    """When matched_chunk_text_display exists, the report must use it verbatim."""
    service = TemplateReportService()
    result = service.generate_report(
        "s1",
        {
            "global_similarity_score": 0.7922,
            "matches": [
                {
                    "filename": "x.pdf",
                    "matched_chunk_text": (
                        "Document cree pour detection de vulgarite en francais"
                    ),
                    "matched_chunk_text_display": (
                        "Document créé pour détection de vulgarité en français"
                    ),
                    "similarity_score": 0.7922,
                }
            ],
        },
        _empty_profanity(),
        _empty_adult(),
        {},
    )

    block = result["plagiarism_explanation"]
    for accented in ("créé", "détection", "vulgarité", "français"):
        assert accented in block, f"missing accented token: {accented!r}"
    # The non-accented version must not leak when the display is provided.
    assert "cree" not in block
    assert "detection" not in block
    assert "vulgarite" not in block


def test_generated_report_never_contains_python_none_token() -> None:
    """Defensive: report must not bleed Python None into user-facing text."""
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.5,
            "matches": [
                {
                    "filename": "x.pdf",
                    "matched_scenario_id": None,
                    "matched_chunk_text": "extrait",
                    "similarity_score": 0.5,
                }
            ],
        },
        profanity_result={
            "contains_profanity": True,
            "profanity_score": 5.0,
            "detected_words": ["mot"],
            "vulgarity_matches": [
                {"word": "mot", "language": None, "category": None,
                 "snippet": "... mot ..."},
            ],
        },
        adult_content_result=_empty_adult(),
        document_stats={},
    )

    report = result["generated_report"]
    # No leaked None tokens in lines that are not part of accented words.
    for line in report.splitlines():
        if "None" in line:
            pytest.fail(f"Found 'None' leak in report line: {line!r}")


def test_generated_report_mentions_exact_duplicate_without_partial_matches() -> None:
    service = TemplateReportService()

    result = service.generate_report(
        scenario_id="scenario-1",
        plagiarism_result={
            "global_similarity_score": 0.0,
            "plagiarism_detected": False,
            "exact_duplicate": True,
            "duplicate_count": 5,
            "duplicate_analyses": [
                {
                    "scenario_id": "old-1",
                    "original_filename": "test_scenario.pdf",
                    "stored_filename": "stored.pdf",
                    "created_at": "2026-06-01T10:00:00+00:00",
                }
            ],
            "matches": [],
            "total_matches": 0,
        },
        profanity_result=_empty_profanity(),
        adult_content_result=_empty_adult(),
        document_stats={"original_filename": "test_scenario.pdf"},
    )

    report = result["generated_report"]
    assert result["risk_level"] == "high"
    assert "doublon exact déjà analysé" in result["summary"]
    assert "Doublon exact" in report
    assert "5 fois" in report
    assert "plagiat partiel" in report
    assert "None" not in report

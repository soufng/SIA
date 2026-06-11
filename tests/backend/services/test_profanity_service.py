import json
from pathlib import Path

import pytest

from backend.services.profanity_service import (
    DARIJA_PROFANITY_WORDS,
    ProfanityService,
    extract_context_snippet,
    normalize_arabic_text,
)


def write_json(file_path: Path, data: object) -> None:
    file_path.write_text(json.dumps(data), encoding="utf-8")


def build_service(tmp_path: Path) -> ProfanityService:
    french_path = tmp_path / "vulgarity_fr.json"
    darija_path = tmp_path / "vulgarity_darija.json"
    write_json(french_path, {"words": ["grossier", "insulte"], "expressions": ["mot sale"]})
    write_json(darija_path, ["khayeb", "hchouma"])
    return ProfanityService(
        french_list_path=french_path,
        darija_list_path=darija_path,
    )


def test_analyze_text_detects_profanity_case_insensitive(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Ce texte contient une INSULTE.")

    assert result["contains_profanity"] is True
    assert result["detected_words"] == ["insulte"]
    assert result["occurrences_count"] == 1
    assert result["profanity_score"] > 0


def test_analyze_text_detects_expressions(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Ce passage contient un mot   sale.")

    assert result["contains_profanity"] is True
    assert result["detected_words"] == ["mot sale"]
    assert result["occurrences_count"] == 1


def test_analyze_text_avoids_simple_false_positives(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Le mot grossierement ne doit pas matcher grossier.")

    assert result["contains_profanity"] is True
    assert result["detected_words"] == ["grossier"]
    assert result["occurrences_count"] == 1


def test_analyze_text_returns_clean_result_for_empty_text(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("   ")

    assert result["contains_profanity"] is False
    assert result["profanity_score"] == 0.0
    assert result["detected_words"] == []
    assert result["occurrences_count"] == 0
    assert result["vulgarity_matches"] == []
    assert result["vulgarity_found_words"] == []
    assert result["vulgarity_categories"] == []


def test_analyze_text_returns_clean_result_when_no_profanity(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Un scenario propre et calme.")

    assert result["contains_profanity"] is False
    assert result["profanity_score"] == 0.0
    assert result["detected_words"] == []
    assert result["occurrences_count"] == 0
    assert result["vulgarity_matches"] == []
    assert result["vulgarity_found_words"] == []
    assert result["vulgarity_categories"] == []


def test_analyze_text_counts_multiple_occurrences(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("grossier insulte grossier")

    assert result["contains_profanity"] is True
    assert result["occurrences_count"] == 3
    assert result["profanity_score"] == 100.0


def test_analyze_text_detects_mixed_french_arabic_and_darija(tmp_path: Path) -> None:
    french_path = tmp_path / "vulgarity_fr.json"
    arabic_path = tmp_path / "vulgarity_ar.json"
    darija_path = tmp_path / "vulgarity_darija.json"
    write_json(french_path, {"insults": [{"term": "insulte", "weight": 1}]})
    write_json(arabic_path, {"insults": [{"term": "غبي", "weight": 3}]})
    write_json(darija_path, {"insults": [{"term": "حمار", "weight": 2}]})
    service = ProfanityService(
        french_list_path=french_path,
        arabic_list_path=arabic_path,
        darija_list_path=darija_path,
    )

    result = service.analyze_text("INSULTE واضحة، وغَبِي ثم حمار.")

    assert result["contains_profanity"] is True
    assert set(result["detected_words"]) == {"insulte", "غبي", "حمار"}
    assert result["occurrences_count"] == 3
    assert result["weighted_score"] == 6
    assert result["profanity_score"] > 0


def test_analyze_text_raises_type_error_for_non_string_text(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    with pytest.raises(TypeError, match="text must be a string"):
        service.analyze_text(None)  # type: ignore[arg-type]


def test_service_raises_file_not_found_for_missing_list(tmp_path: Path) -> None:
    french_path = tmp_path / "vulgarity_fr.json"
    darija_path = tmp_path / "missing.json"
    write_json(french_path, ["grossier"])

    with pytest.raises(FileNotFoundError, match="Profanity list file not found"):
        ProfanityService(french_list_path=french_path, darija_list_path=darija_path)


def test_service_raises_value_error_for_invalid_json(tmp_path: Path) -> None:
    french_path = tmp_path / "vulgarity_fr.json"
    darija_path = tmp_path / "vulgarity_darija.json"
    french_path.write_text("{invalid", encoding="utf-8")
    write_json(darija_path, ["khayeb"])

    with pytest.raises(ValueError, match="Invalid JSON file"):
        ProfanityService(french_list_path=french_path, darija_list_path=darija_path)


def test_service_raises_value_error_for_empty_lists(tmp_path: Path) -> None:
    french_path = tmp_path / "vulgarity_fr.json"
    darija_path = tmp_path / "vulgarity_darija.json"
    write_json(french_path, [])
    write_json(darija_path, {"words": []})

    with pytest.raises(ValueError, match="Profanity list contains no valid terms"):
        ProfanityService(french_list_path=french_path, darija_list_path=darija_path)


# ---------- Darija / Arabic detection ----------


def test_normalize_arabic_strips_diacritics_and_tatweel() -> None:
    assert normalize_arabic_text("قَحَــــاب") == normalize_arabic_text("قحاب")
    assert normalize_arabic_text("إنسان") == normalize_arabic_text("انسان")
    assert normalize_arabic_text("مدرسة") == normalize_arabic_text("مدرسه")


def test_extract_context_snippet_returns_phrase_around_word() -> None:
    text = "Phrase neutre. Le mot CIBLE est ici. Autre phrase."
    start = text.index("CIBLE")
    end = start + len("CIBLE")

    snippet = extract_context_snippet(text, start, end, window=80)

    assert "CIBLE" in snippet
    assert "Le mot CIBLE est ici." in snippet
    # Not the whole document so ellipsis on both sides.
    assert snippet.startswith("...")
    assert snippet.endswith("...")


def test_analyze_text_detects_darija_arabic_words(tmp_path: Path) -> None:
    french_path = tmp_path / "vulgarity_fr.json"
    darija_path = tmp_path / "vulgarity_darija.json"
    write_json(french_path, ["grossier"])
    # Lexicon does not include the new words; fallback set must catch them.
    write_json(darija_path, ["hchouma"])
    service = ProfanityService(
        french_list_path=french_path,
        darija_list_path=darija_path,
        use_wiqaya=False,
    )

    text = "هذا اختبار فيه قحاب زوامل زامل داخل فقرة قصيرة."
    result = service.analyze_text(text)

    assert result["profanity_score"] > 0
    found = set(result["vulgarity_found_words"])
    assert {"قحاب", "زوامل", "زامل"}.issubset(found)
    matches = result["vulgarity_matches"]
    assert len(matches) >= 3
    for match in matches:
        assert {"word", "snippet", "start", "end"}.issubset(match)
        assert match["snippet"]
        assert match["word"] in match["snippet"]
        assert text[match["start"] : match["end"]] == match["word"]


def test_analyze_text_detects_arabic_with_diacritics(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    text = "نص يحوي قَــــحَاب وكلمات أخرى."

    result = service.analyze_text(text)

    assert result["contains_profanity"] is True
    assert any(
        normalize_arabic_text(m["word"]) == "قحاب"
        for m in result["vulgarity_matches"]
    )


def test_analyze_text_detects_latin_darija_transliterations(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    text = "Some content with zamel and 9hab in it!"

    result = service.analyze_text(text)

    found = {m["word"].lower() for m in result["vulgarity_matches"]}
    assert "zamel" in found
    assert "9hab" in found
    for match in result["vulgarity_matches"]:
        assert match["snippet"]
        assert match["word"].lower() in match["snippet"].lower()


def test_darija_profanity_words_set_is_complete() -> None:
    expected = {"قحاب", "زوامل", "زامل", "9hab", "qhab", "zamel", "zawamel", "zwamel"}
    assert expected.issubset(DARIJA_PROFANITY_WORDS)


def test_analyze_text_handles_punctuation_after_word(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    text = "بداية، قحاب! وانتهى."

    result = service.analyze_text(text)

    assert any(m["word"] == "قحاب" for m in result["vulgarity_matches"])

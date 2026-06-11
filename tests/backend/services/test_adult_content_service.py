import json
from pathlib import Path

import pytest

from backend.services.adult_content_service import AdultContentService


def write_json(file_path: Path, data: object) -> None:
    file_path.write_text(json.dumps(data), encoding="utf-8")


def build_service(tmp_path: Path) -> AdultContentService:
    french_path = tmp_path / "adult_fr.json"
    darija_path = tmp_path / "adult_darija.json"
    write_json(
        french_path,
        {
            "terms": ["adulte", "explicite"],
            "expressions": ["contenu sensible"],
        },
    )
    write_json(darija_path, ["kbar", "hchouma kbira"])
    return AdultContentService(
        french_list_path=french_path,
        darija_list_path=darija_path,
    )


def test_analyze_text_detects_adult_content_case_insensitive(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Ce passage contient un terme EXPLICITE.")

    assert result["contains_adult_content"] is True
    assert result["risk_level"] == "medium"
    assert result["detected_terms"] == ["explicite"]
    assert result["occurrences_count"] == 1
    assert result["adult_content_score"] > 0


def test_analyze_text_detects_multi_word_expressions(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Ce texte contient un contenu   sensible.")

    assert result["contains_adult_content"] is True
    assert result["detected_terms"] == ["contenu sensible"]
    assert result["occurrences_count"] == 1


def test_analyze_text_avoids_simple_false_positives(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Le mot explicitement ne doit pas matcher explicite.")

    assert result["contains_adult_content"] is True
    assert result["detected_terms"] == ["explicite"]
    assert result["occurrences_count"] == 1


def test_analyze_text_returns_low_risk_clean_result_for_empty_text(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("   ")

    assert result == {
        "contains_adult_content": False,
        "risk_level": "low",
        "adult_content_score": 0.0,
        "detected_terms": [],
        "occurrences_count": 0,
        "nudity_matches": [],
    }


def test_analyze_text_returns_clean_result_when_no_adult_content(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("Un scenario familial et neutre.")

    assert result == {
        "contains_adult_content": False,
        "risk_level": "low",
        "adult_content_score": 0.0,
        "detected_terms": [],
        "occurrences_count": 0,
        "nudity_matches": [],
    }


def test_analyze_text_returns_high_risk_for_dense_occurrences(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    result = service.analyze_text("adulte explicite adulte")

    assert result["contains_adult_content"] is True
    assert result["risk_level"] == "high"
    assert result["adult_content_score"] == 100.0
    assert result["occurrences_count"] == 3


def test_analyze_text_uses_weighted_multilingual_lexicons(tmp_path: Path) -> None:
    french_path = tmp_path / "adult_fr.json"
    arabic_path = tmp_path / "adult_ar.json"
    darija_path = tmp_path / "adult_darija.json"
    write_json(french_path, {"sexual_terms": [{"term": "explicite", "weight": 3}]})
    write_json(arabic_path, {"sexual_terms": [{"term": "علاقة جنسية", "weight": 4}]})
    write_json(darija_path, {"solicitation_terms": [{"term": "بورنو", "weight": 5}]})
    service = AdultContentService(
        french_list_path=french_path,
        arabic_list_path=arabic_path,
        darija_list_path=darija_path,
    )

    result = service.analyze_text("Contenu EXPLICITE مع علاقة جنسية وكلمة بورنو.")

    assert result["contains_adult_content"] is True
    assert set(result["detected_terms"]) == {"explicite", "علاقة جنسية", "بورنو"}
    assert result["occurrences_count"] == 3
    assert result["weighted_score"] == 12
    assert result["risk_level"] in {"medium", "high"}


def test_analyze_text_raises_type_error_for_non_string_text(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    with pytest.raises(TypeError, match="text must be a string"):
        service.analyze_text(None)  # type: ignore[arg-type]


def test_service_raises_file_not_found_for_missing_list(tmp_path: Path) -> None:
    french_path = tmp_path / "adult_fr.json"
    darija_path = tmp_path / "missing.json"
    write_json(french_path, ["adulte"])

    with pytest.raises(FileNotFoundError, match="Adult-content list file not found"):
        AdultContentService(french_list_path=french_path, darija_list_path=darija_path)


def test_service_raises_value_error_for_invalid_json(tmp_path: Path) -> None:
    french_path = tmp_path / "adult_fr.json"
    darija_path = tmp_path / "adult_darija.json"
    french_path.write_text("{invalid", encoding="utf-8")
    write_json(darija_path, ["kbar"])

    with pytest.raises(ValueError, match="Invalid JSON file"):
        AdultContentService(french_list_path=french_path, darija_list_path=darija_path)


def test_service_raises_value_error_for_empty_lists(tmp_path: Path) -> None:
    french_path = tmp_path / "adult_fr.json"
    darija_path = tmp_path / "adult_darija.json"
    write_json(french_path, [])
    write_json(darija_path, {"terms": []})

    with pytest.raises(ValueError, match="Adult-content list contains no valid terms"):
        AdultContentService(french_list_path=french_path, darija_list_path=darija_path)

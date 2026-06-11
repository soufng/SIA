from backend.services.text_cleaning_service import TextCleaningService


def test_clean_text_returns_empty_string_when_text_is_none() -> None:
    service = TextCleaningService()

    result = service.clean_text(None)

    assert result == ""


def test_clean_text_returns_empty_string_when_text_is_empty() -> None:
    service = TextCleaningService()

    result = service.clean_text("   \n\t   ")

    assert result == ""


def test_clean_text_removes_multiple_spaces_and_tabs() -> None:
    service = TextCleaningService()

    result = service.clean_text("Un   scenario\t\tavec    espaces.")

    assert result == "Un scenario avec espaces."


def test_clean_text_replaces_multiple_newlines_with_single_newline() -> None:
    service = TextCleaningService()

    result = service.clean_text("Scene 1\n\n\nScene 2\n\nScene 3")

    assert result == "Scene 1\nScene 2\nScene 3"


def test_clean_text_normalizes_unicode_nfkc() -> None:
    service = TextCleaningService()

    result = service.clean_text("Ｓｃｅｎａｒｉｏ ①")

    assert result == "Scenario 1"


def test_clean_text_removes_unnecessary_control_characters() -> None:
    service = TextCleaningService()

    result = service.clean_text("Scene\x00 importante\x08\nDialogue\x7f final")

    assert result == "Scene importante\nDialogue final"


def test_clean_text_strips_text_edges() -> None:
    service = TextCleaningService()

    result = service.clean_text("   Debut du scenario.   ")

    assert result == "Debut du scenario."


def test_clean_text_combines_all_cleaning_rules() -> None:
    service = TextCleaningService()

    result = service.clean_text("  Ｔｉｔｒｅ\t\t\x00\n\n\nScene   1   ")

    assert result == "Titre\nScene 1"
def test_find_and_remove_repeated_boilerplate_lines() -> None:
    service = TextCleaningService()
    repeated = (
        "Texte de remplissage non commun: cette ligne existe seulement pour "
        "occuper la page et garder une structure stable."
    )
    text = "\n".join(
        [
            repeated,
            "Passage informatif 1 avec des details uniques.",
            repeated,
            "Passage informatif 2 avec une autre information.",
            repeated,
        ]
    )

    repeated_lines = service.find_repeated_boilerplate_lines(text)
    cleaned = service.remove_boilerplate_lines(text, repeated_lines)

    assert any("texte de remplissage" in line for line in repeated_lines)
    assert "Texte de remplissage" not in cleaned
    assert "Passage informatif 1" in cleaned
    assert "Passage informatif 2" in cleaned


def test_boilerplate_ratio_counts_repeated_lines() -> None:
    service = TextCleaningService()
    repeated = "Header technique repete pour toutes les pages du document."
    text = "\n".join([repeated, "Contenu utile vraiment distinct.", repeated])
    repeated_lines = service.find_repeated_boilerplate_lines(text, min_count=2)

    assert service.boilerplate_ratio(text, repeated_lines) == 0.6667

import pytest

from backend.services.chunking_service import ChunkingService


def test_chunk_text_returns_empty_list_for_empty_text() -> None:
    service = ChunkingService()

    result = service.chunk_text("")

    assert result == []


def test_chunk_text_returns_empty_list_for_whitespace_text() -> None:
    service = ChunkingService()

    result = service.chunk_text("   \n\t   ")

    assert result == []


def test_chunk_text_returns_single_chunk_for_short_text() -> None:
    service = ChunkingService()

    result = service.chunk_text("un deux trois", chunk_size=400, overlap=50)

    assert result == ["un deux trois"]


def test_chunk_text_splits_text_based_on_words() -> None:
    service = ChunkingService()
    text = " ".join(str(index) for index in range(1, 11))

    result = service.chunk_text(text, chunk_size=4, overlap=0)

    assert result == ["1 2 3 4", "5 6 7 8", "9 10"]


def test_chunk_text_applies_overlap_between_consecutive_chunks() -> None:
    service = ChunkingService()
    text = " ".join(str(index) for index in range(1, 11))

    result = service.chunk_text(text, chunk_size=4, overlap=2)

    assert result == [
        "1 2 3 4",
        "3 4 5 6",
        "5 6 7 8",
        "7 8 9 10",
        "9 10",
    ]


def test_chunk_text_preserves_word_order() -> None:
    service = ChunkingService()
    text = "alpha beta gamma delta epsilon"

    result = service.chunk_text(text, chunk_size=3, overlap=1)

    assert result == ["alpha beta gamma", "gamma delta epsilon", "epsilon"]


def test_chunk_text_does_not_create_empty_chunks() -> None:
    service = ChunkingService()

    result = service.chunk_text("alpha beta", chunk_size=3, overlap=1)

    assert result == ["alpha beta"]
    assert all(chunk for chunk in result)


def test_chunk_text_raises_type_error_when_text_is_not_string() -> None:
    service = ChunkingService()

    with pytest.raises(TypeError, match="text must be a string"):
        service.chunk_text(None)  # type: ignore[arg-type]


def test_chunk_text_raises_value_error_when_chunk_size_is_zero() -> None:
    service = ChunkingService()

    with pytest.raises(ValueError, match="chunk_size must be greater than 0"):
        service.chunk_text("alpha beta", chunk_size=0, overlap=0)


def test_chunk_text_raises_value_error_when_overlap_is_negative() -> None:
    service = ChunkingService()

    with pytest.raises(ValueError, match="overlap must be greater than or equal to 0"):
        service.chunk_text("alpha beta", chunk_size=3, overlap=-1)


def test_chunk_text_raises_value_error_when_overlap_is_equal_to_chunk_size() -> None:
    service = ChunkingService()

    with pytest.raises(ValueError, match="overlap must be smaller than chunk_size"):
        service.chunk_text("alpha beta", chunk_size=3, overlap=3)


def test_chunk_text_raises_value_error_when_overlap_is_greater_than_chunk_size() -> None:
    service = ChunkingService()

    with pytest.raises(ValueError, match="overlap must be smaller than chunk_size"):
        service.chunk_text("alpha beta", chunk_size=3, overlap=4)


def test_chunk_pages_with_metadata_splits_long_page_into_passage_chunks() -> None:
    service = ChunkingService()
    text = " ".join(f"mot{i}" for i in range(120))

    chunks = service.chunk_pages_with_metadata(
        [{"page_number": 3, "text_normalized": text, "text_display": text}],
        chunk_size=50,
        overlap=10,
        min_chunk_size=20,
    )

    assert len(chunks) >= 3
    assert chunks[0]["page_number"] == 3
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["chunk_id"] == "chunk_0"
    assert chunks[0]["start_offset"] == 0
    assert chunks[1]["start_offset"] == 40
    assert chunks[0]["word_count"] == 50


def test_chunk_pages_with_metadata_keeps_separate_pages_distinct() -> None:
    service = ChunkingService()

    chunks = service.chunk_pages_with_metadata(
        [
            {"page_number": 1, "text_normalized": "alpha " * 60, "text_display": "alpha " * 60},
            {"page_number": 2, "text_normalized": "beta " * 60, "text_display": "beta " * 60},
        ],
        chunk_size=80,
        overlap=20,
        min_chunk_size=20,
    )

    assert {chunk["page_number"] for chunk in chunks} == {1, 2}
    assert chunks[0]["chunk_index"] != chunks[-1]["chunk_index"]

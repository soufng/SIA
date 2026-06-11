"""Tests for the parallel normalized/display chunking helper."""

import pytest

from backend.services.chunking_service import ChunkingService


def test_chunk_text_with_display_returns_parallel_chunks() -> None:
    service = ChunkingService()
    normalized = "document de test cree pour detection de vulgarite en francais"
    display = "Document de test créé pour détection de vulgarité en français"

    pairs = service.chunk_text_with_display(
        text_normalized=normalized,
        text_display=display,
        chunk_size=6,
        overlap=2,
    )

    assert len(pairs) >= 1
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in pairs)
    # First chunk: normalized is lowercase ASCII, display keeps accents/case.
    norm0, disp0 = pairs[0]
    assert "cree" in norm0
    assert "créé" in disp0
    assert "détection" in disp0
    # Word boundaries align: same number of words per chunk.
    assert len(norm0.split()) == len(disp0.split())


def test_chunk_text_with_display_falls_back_when_word_counts_differ() -> None:
    service = ChunkingService()
    normalized = "alpha beta gamma delta"
    display = "alpha beta gamma"  # one word missing

    pairs = service.chunk_text_with_display(
        text_normalized=normalized,
        text_display=display,
        chunk_size=4,
        overlap=0,
    )

    # Fallback: display chunks are identical to normalized chunks.
    for normalized_chunk, display_chunk in pairs:
        assert normalized_chunk == display_chunk


def test_chunk_text_with_display_accepts_none_display() -> None:
    service = ChunkingService()
    pairs = service.chunk_text_with_display(
        text_normalized="alpha beta gamma",
        text_display=None,
        chunk_size=3,
        overlap=0,
    )
    assert pairs
    for norm, disp in pairs:
        assert norm == disp


def test_chunk_text_with_display_empty_input() -> None:
    service = ChunkingService()
    assert (
        service.chunk_text_with_display(
            text_normalized="",
            text_display="",
            chunk_size=10,
            overlap=2,
        )
        == []
    )


def test_chunk_text_with_display_validates_inputs() -> None:
    service = ChunkingService()
    with pytest.raises(ValueError):
        service.chunk_text_with_display(
            text_normalized="hello world",
            text_display="hello world",
            chunk_size=0,
            overlap=0,
        )

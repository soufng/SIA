from unittest.mock import Mock

import pytest

from backend.services.plagiarism_service import PlagiarismService

# Texts with enough shared informative tokens (5+ consecutive) to satisfy
# the MIN_TEXTUAL_EVIDENCE filter and the is_likely_false_positive Rule C.
# Words from SCENARIO_STOPWORDS (texte, ligne, page, salon, appartement …)
# are filtered out before scoring, so we use unique domain vocabulary.
_SHARED_PASSAGE = (
    "Yasmine decouvre boite cachee plancher ancien grenier immeuble fouille"
)
_CURRENT_CHUNK = _SHARED_PASSAGE + " enquete approfondie"
_SOURCE_CHUNK = _SHARED_PASSAGE + " pendant inspection nocturne"


def build_service(
    embedding_service: Mock | None = None,
    vector_service: Mock | None = None,
) -> PlagiarismService:
    return PlagiarismService(
        embedding_service=embedding_service or Mock(),
        vector_service=vector_service or Mock(),
    )


def test_analyze_chunks_returns_expected_result_when_no_matches_are_found() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1, 0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = []
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks("scenario-1", ["chunk original"])

    assert result == {
        "scenario_id": "scenario-1",
        "global_similarity_score": 0.0,
        "plagiarism_detected": False,
        "matches": [],
    }


def test_analyze_chunks_returns_suspicious_matches_above_threshold() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1, 0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = [
        {
            "id": "point-1",
            "score": 0.91,
            "payload": {
                "scenario_id": "scenario-2",
                "chunk_id": "scenario-2_0",
                "chunk_text": _SOURCE_CHUNK,
            },
        }
    ]
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks(
        "scenario-1",
        [_CURRENT_CHUNK],
        similarity_threshold=0.75,
        top_k=5,
    )

    assert result["scenario_id"] == "scenario-1"
    assert result["global_similarity_score"] == 0.91
    assert result["plagiarism_detected"] is True
    match = result["matches"][0]
    assert match["chunk_index"] == 0
    assert match["current_chunk_id"] == "scenario-1_0"
    assert match["current_chunk_index"] == 0
    assert match["chunk_text"] == _CURRENT_CHUNK
    assert match["matched_scenario_id"] == "scenario-2"
    assert match["matched_chunk_id"] == "scenario-2_0"
    assert match["source_chunk_id"] == "scenario-2_0"
    assert match["matched_chunk_text"] == _SOURCE_CHUNK
    assert match["matched_chunk_text_display"] == _SOURCE_CHUNK
    assert match["snippet"]
    assert match["current_page_number"] is None
    assert match["source_page_number"] is None
    assert match["similarity_score"] == 0.91
    assert "match_quality_score" in match
    assert "boilerplate_ratio" in match
    assert "informative_word_count" in match


def test_analyze_chunks_propagates_chunk_metadata() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1, 0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = [
        {
            "id": "point-1",
            "score": 0.91,
            "payload": {
                "scenario_id": "scenario-2",
                "chunk_id": "source_7",
                "chunk_index": 7,
                "page_number": 4,
                "start_offset": 100,
                "end_offset": 150,
                "word_count": 50,
                "chunk_text": _SOURCE_CHUNK,
            },
        }
    ]
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks(
        "scenario-1",
        [_CURRENT_CHUNK],
        chunk_metadata=[
            {
                "chunk_id": "current_3",
                "chunk_index": 3,
                "page_number": 2,
                "start_offset": 10,
                "end_offset": 55,
                "word_count": 45,
            }
        ],
    )

    match = result["matches"][0]
    assert match["current_chunk_id"] == "current_3"
    assert match["current_chunk_index"] == 3
    assert match["source_chunk_id"] == "source_7"
    assert match["source_chunk_index"] == 7
    assert match["current_page_number"] == 2
    assert match["source_page_number"] == 4
    assert match["start_offset"] == 10
    assert match["source_start_offset"] == 100


def test_analyze_chunks_penalizes_boilerplate_payload() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1, 0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = [
        {
            "id": "point-1",
            "score": 0.95,
            "payload": {
                "scenario_id": "scenario-2",
                "chunk_id": "source_1",
                "chunk_text": _SOURCE_CHUNK,
                "boilerplate_ratio": 0.8,
            },
        }
    ]
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks("scenario-1", [_CURRENT_CHUNK])

    match = result["matches"][0]
    assert match["boilerplate_ratio"] == 0.8
    assert match["match_quality_score"] < match["similarity_score"]


def test_analyze_chunks_ignores_matches_from_same_scenario() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1, 0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = [
        {
            "score": 0.99,
            "payload": {
                "scenario_id": "scenario-1",
                "chunk_id": "scenario-1_0",
                "chunk_text": "meme scenario",
            },
        }
    ]
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks("scenario-1", ["chunk original"])

    assert result["plagiarism_detected"] is False
    assert result["matches"] == []


def test_analyze_chunks_ignores_matches_below_threshold() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1, 0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = [
        {
            "score": 0.74,
            "payload": {
                "scenario_id": "scenario-2",
                "chunk_id": "scenario-2_0",
                "chunk_text": "chunk proche",
            },
        }
    ]
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks(
        "scenario-1",
        ["chunk original"],
        similarity_threshold=0.75,
    )

    assert result["global_similarity_score"] == 0.0
    assert result["matches"] == []


def test_analyze_chunks_calculates_global_score_from_best_match_per_chunk() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1], [0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.side_effect = [
        [
            {
                "score": 0.8,
                "payload": {
                    "scenario_id": "scenario-2",
                    "chunk_id": "scenario-2_0",
                    "chunk_text": _SOURCE_CHUNK,
                },
            },
            {
                "score": 0.9,
                "payload": {
                    "scenario_id": "scenario-3",
                    "chunk_id": "scenario-3_0",
                    "chunk_text": _SOURCE_CHUNK + " variante",
                },
            },
        ],
        [],
    ]
    service = build_service(embedding_service, vector_service)

    result = service.analyze_chunks("scenario-1", [_CURRENT_CHUNK, _CURRENT_CHUNK])

    assert result["global_similarity_score"] == 0.45
    assert len(result["matches"]) == 2


def test_analyze_chunks_calls_dependencies_with_expected_arguments() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.return_value = [[0.1], [0.2]]
    vector_service = Mock()
    vector_service.search_similar_chunks.return_value = []
    service = build_service(embedding_service, vector_service)

    service.analyze_chunks("scenario-1", ["chunk 1", "chunk 2"], top_k=3)

    embedding_service.generate_embeddings.assert_called_once_with(
        ["chunk 1", "chunk 2"], is_query=True
    )
    assert vector_service.search_similar_chunks.call_count == 2
    vector_service.search_similar_chunks.assert_any_call(embedding=[0.1], limit=3)
    vector_service.search_similar_chunks.assert_any_call(embedding=[0.2], limit=3)


def test_analyze_chunks_raises_value_error_for_empty_scenario_id() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="scenario_id must not be empty"):
        service.analyze_chunks("   ", ["chunk"])


def test_analyze_chunks_raises_type_error_when_chunks_is_not_a_list() -> None:
    service = build_service()

    with pytest.raises(TypeError, match="chunks must be a list of strings"):
        service.analyze_chunks("scenario-1", "chunk")  # type: ignore[arg-type]


def test_analyze_chunks_raises_value_error_for_empty_chunks() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="chunks must not be empty"):
        service.analyze_chunks("scenario-1", [])


def test_analyze_chunks_raises_type_error_for_non_string_chunk() -> None:
    service = build_service()

    with pytest.raises(TypeError, match="all chunks must be strings"):
        service.analyze_chunks("scenario-1", ["chunk", None])  # type: ignore[list-item]


def test_analyze_chunks_raises_value_error_for_empty_chunk() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="chunk at index 1 must not be empty"):
        service.analyze_chunks("scenario-1", ["chunk", "   "])


def test_analyze_chunks_raises_value_error_for_invalid_threshold() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="similarity_threshold must be between 0 and 1"):
        service.analyze_chunks("scenario-1", ["chunk"], similarity_threshold=1.2)


def test_analyze_chunks_raises_value_error_for_invalid_top_k() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="top_k must be greater than 0"):
        service.analyze_chunks("scenario-1", ["chunk"], top_k=0)


def test_analyze_chunks_wraps_dependency_errors() -> None:
    embedding_service = Mock()
    embedding_service.generate_embeddings.side_effect = Exception("model unavailable")
    service = build_service(embedding_service=embedding_service)

    with pytest.raises(RuntimeError, match="Failed to analyze plagiarism"):
        service.analyze_chunks("scenario-1", ["chunk"])

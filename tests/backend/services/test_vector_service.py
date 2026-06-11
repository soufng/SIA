from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from qdrant_client.http import models

from backend.services.vector_service import VectorService


def build_service(mock_client: Mock) -> VectorService:
    return VectorService(
        client=mock_client,
        collection_name="test_collection",
        vector_size=3,
    )


def test_create_collection_creates_collection_when_missing() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = False

    build_service(mock_client)

    mock_client.create_collection.assert_called_once()
    _, kwargs = mock_client.create_collection.call_args
    assert kwargs["collection_name"] == "test_collection"
    assert kwargs["vectors_config"].size == 3
    assert kwargs["vectors_config"].distance == models.Distance.COSINE


def test_create_collection_does_not_create_existing_collection() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True

    build_service(mock_client)

    mock_client.create_collection.assert_not_called()


def test_upsert_chunks_stores_payloads() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    service = build_service(mock_client)

    service.upsert_chunks(
        scenario_id="scenario-1",
        chunks=["chunk un", "chunk deux"],
        embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
    )

    mock_client.upsert.assert_called_once()
    _, kwargs = mock_client.upsert.call_args
    points = kwargs["points"]
    assert kwargs["collection_name"] == "test_collection"
    assert len(points) == 2
    assert points[0].payload == {
        "scenario_id": "scenario-1",
        "chunk_id": "scenario-1_0",
        "chunk_text": "chunk un",
        "chunk_index": 0,
        "page_number": None,
        "start_offset": None,
        "end_offset": None,
        "word_count": None,
        "boilerplate_ratio": 0.0,
    }
    assert points[0].vector == [0.1, 0.2, 0.3]


def test_upsert_chunks_stores_chunk_metadata() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    service = build_service(mock_client)

    service.upsert_chunks(
        scenario_id="scenario-1",
        chunks=["chunk un"],
        embeddings=[[0.1, 0.2, 0.3]],
        display_chunks=["Chunk un"],
        chunk_metadata=[
            {
                "chunk_id": "custom-0",
                "chunk_index": 4,
                "page_number": 2,
                "start_offset": 10,
                "end_offset": 30,
                "word_count": 20,
            }
        ],
    )

    point = mock_client.upsert.call_args.kwargs["points"][0]
    assert point.payload["chunk_id"] == "custom-0"
    assert point.payload["chunk_index"] == 4
    assert point.payload["page_number"] == 2
    assert point.payload["chunk_text_display"] == "Chunk un"


def test_search_similar_chunks_returns_standard_dicts() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    mock_client.search.return_value = [
        SimpleNamespace(
            id="point-1",
            score=0.98,
            payload={
                "scenario_id": "scenario-1",
                "chunk_id": "scenario-1_0",
                "chunk_text": "chunk un",
            },
        )
    ]
    service = build_service(mock_client)

    result = service.search_similar_chunks([0.1, 0.2, 0.3], limit=5)

    assert result == [
        {
            "id": "point-1",
            "score": 0.98,
            "payload": {
                "scenario_id": "scenario-1",
                "chunk_id": "scenario-1_0",
                "chunk_text": "chunk un",
            },
        }
    ]
    mock_client.search.assert_called_once_with(
        collection_name="test_collection",
        query_vector=[0.1, 0.2, 0.3],
        limit=5,
        with_payload=True,
    )


def test_delete_scenario_vectors_deletes_by_scenario_id() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    service = build_service(mock_client)

    service.delete_scenario_vectors("scenario-1")

    mock_client.delete.assert_called_once()
    _, kwargs = mock_client.delete.call_args
    assert kwargs["collection_name"] == "test_collection"
    selector = kwargs["points_selector"]
    assert selector.filter.must[0].key == "scenario_id"
    assert selector.filter.must[0].match.value == "scenario-1"


def test_upsert_chunks_raises_value_error_for_mismatched_lengths() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    service = build_service(mock_client)

    with pytest.raises(ValueError, match="chunks and embeddings must have the same length"):
        service.upsert_chunks("scenario-1", ["chunk"], [[0.1], [0.2]])


def test_search_similar_chunks_raises_value_error_for_invalid_limit() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    service = build_service(mock_client)

    with pytest.raises(ValueError, match="limit must be greater than 0"):
        service.search_similar_chunks([0.1, 0.2, 0.3], limit=0)


def test_delete_scenario_vectors_raises_value_error_for_empty_scenario_id() -> None:
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    service = build_service(mock_client)

    with pytest.raises(ValueError, match="scenario_id must not be empty"):
        service.delete_scenario_vectors("   ")


def test_create_collection_wraps_qdrant_errors() -> None:
    mock_client = Mock()
    mock_client.collection_exists.side_effect = Exception("qdrant unavailable")

    with pytest.raises(RuntimeError, match="Failed to create or verify Qdrant collection"):
        build_service(mock_client)

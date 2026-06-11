from unittest.mock import Mock, patch

import pytest

from backend.services.embedding_service import EmbeddingService


def build_service_with_mock_model(mock_model: Mock) -> EmbeddingService:
    with patch(
        "backend.services.embedding_service.SentenceTransformer",
        return_value=mock_model,
    ):
        return EmbeddingService()


def test_embedding_service_loads_default_model_once_on_initialization() -> None:
    mock_model = Mock()

    with patch(
        "backend.services.embedding_service.SentenceTransformer",
        return_value=mock_model,
    ) as sentence_transformer:
        service = EmbeddingService()

    sentence_transformer.assert_called_once_with(
        "intfloat/multilingual-e5-base"
    )
    assert service.model is mock_model


def test_generate_embedding_returns_python_float_list() -> None:
    mock_model = Mock()
    mock_model.encode.return_value = [1, 2.5, "3.0"]
    service = build_service_with_mock_model(mock_model)

    result = service.generate_embedding("texte de scenario")

    assert result == [1.0, 2.5, 3.0]
    mock_model.encode.assert_called_once_with("passage: texte de scenario")


def test_generate_embedding_converts_numpy_like_output_to_list() -> None:
    mock_embedding = Mock()
    mock_embedding.tolist.return_value = [0.1, 0.2, 0.3]
    mock_model = Mock()
    mock_model.encode.return_value = mock_embedding
    service = build_service_with_mock_model(mock_model)

    result = service.generate_embedding("texte")

    assert result == [0.1, 0.2, 0.3]


def test_generate_embeddings_returns_python_float_vectors() -> None:
    mock_model = Mock()
    mock_model.encode.return_value = [[1, 2], [3.5, 4.5]]
    service = build_service_with_mock_model(mock_model)

    result = service.generate_embeddings(["chunk un", "chunk deux"])

    assert result == [[1.0, 2.0], [3.5, 4.5]]
    mock_model.encode.assert_called_once_with(
        ["passage: chunk un", "passage: chunk deux"]
    )


def test_generate_embedding_raises_type_error_for_non_string_text() -> None:
    service = build_service_with_mock_model(Mock())

    with pytest.raises(TypeError, match="text must be a string"):
        service.generate_embedding(None)  # type: ignore[arg-type]


def test_generate_embedding_raises_value_error_for_empty_text() -> None:
    service = build_service_with_mock_model(Mock())

    with pytest.raises(ValueError, match="text must not be empty"):
        service.generate_embedding("   ")


def test_generate_embeddings_raises_type_error_for_non_list_input() -> None:
    service = build_service_with_mock_model(Mock())

    with pytest.raises(TypeError, match="texts must be a list of strings"):
        service.generate_embeddings("chunk")  # type: ignore[arg-type]


def test_generate_embeddings_raises_value_error_for_empty_list() -> None:
    service = build_service_with_mock_model(Mock())

    with pytest.raises(ValueError, match="texts must not be empty"):
        service.generate_embeddings([])


def test_generate_embeddings_raises_type_error_for_non_string_item() -> None:
    service = build_service_with_mock_model(Mock())

    with pytest.raises(TypeError, match="all texts must be strings"):
        service.generate_embeddings(["chunk", None])  # type: ignore[list-item]


def test_generate_embeddings_raises_value_error_for_empty_item() -> None:
    service = build_service_with_mock_model(Mock())

    with pytest.raises(ValueError, match="texts must not contain empty values"):
        service.generate_embeddings(["chunk", "   "])

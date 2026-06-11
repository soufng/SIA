import logging
from typing import Any

from sentence_transformers import SentenceTransformer

from backend.core.config import settings


logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service responsible for generating vector embeddings from text chunks.

    Supports model families that require asymmetric input prefixes
    (intfloat/multilingual-e5-*, BAAI/bge-*) by inferring the family from
    ``model_name``. Other models behave like sentence-transformers MiniLM.
    """

    def __init__(
        self,
        model_name: str | None = None,
    ) -> None:
        """Initialize the embedding service and load the model once.

        Args:
            model_name: Sentence Transformers model identifier. When ``None``
                the model is read from ``settings.EMBEDDING_MODEL_NAME``.
        """
        resolved_name = model_name or settings.EMBEDDING_MODEL_NAME
        logger.info("Loading Sentence Transformers model: %s", resolved_name)
        self.model_name = resolved_name
        self.model = SentenceTransformer(resolved_name)
        self._prefix_passage, self._prefix_query = self._resolve_prefixes(
            resolved_name
        )
        logger.info(
            "Sentence Transformers model loaded: %s (passage prefix=%r, "
            "query prefix=%r)",
            resolved_name,
            self._prefix_passage,
            self._prefix_query,
        )

    @staticmethod
    def _resolve_prefixes(model_name: str) -> tuple[str, str]:
        """Return (passage_prefix, query_prefix) for the given model name.

        - intfloat/*-e5-* expects ``"passage: "`` / ``"query: "``.
        - BAAI/bge-* (encoder-only family) does not need a prefix for
          passages and only an optional instruction for queries — we keep
          both empty to preserve cosine alignment with what was indexed.
        - Anything else: no prefix.
        """
        lower = model_name.lower()
        if "-e5-" in lower or lower.endswith("e5") or "e5-" in lower:
            return "passage: ", "query: "
        return "", ""

    def generate_embedding(self, text: str, is_query: bool = False) -> list[float]:
        """Generate an embedding vector for a single text.

        Args:
            text: Text chunk to transform into an embedding.
            is_query: When True, apply the query prefix instead of the
                passage prefix (for E5-style asymmetric models).

        Returns:
            A Python list of floats representing the embedding vector.

        Raises:
            TypeError: If text is not a string.
            ValueError: If text is empty or contains only whitespace.
        """
        self._validate_text(text)

        logger.info("Generating embedding for one text chunk.")
        prefix = self._prefix_query if is_query else self._prefix_passage
        embedding = self.model.encode(f"{prefix}{text}" if prefix else text)
        return self._to_float_list(embedding)

    def generate_embeddings(
        self, texts: list[str], is_query: bool = False
    ) -> list[list[float]]:
        """Generate embedding vectors for multiple texts.

        Args:
            texts: List of text chunks to transform into embeddings.
            is_query: When True, apply the query prefix instead of the
                passage prefix (for E5-style asymmetric models).

        Returns:
            A list of Python float vectors.

        Raises:
            TypeError: If texts is not a list or contains non-string values.
            ValueError: If texts is empty or contains empty text values.
        """
        self._validate_texts(texts)

        logger.info("Generating embeddings for %s text chunks.", len(texts))
        prefix = self._prefix_query if is_query else self._prefix_passage
        prepared = [f"{prefix}{t}" for t in texts] if prefix else texts
        embeddings = self.model.encode(prepared)
        return [self._to_float_list(embedding) for embedding in embeddings]

    def _validate_text(self, text: str) -> None:
        """Validate a single text input.

        Args:
            text: Text value to validate.

        Raises:
            TypeError: If text is not a string.
            ValueError: If text is empty or contains only whitespace.
        """
        if not isinstance(text, str):
            logger.error("Invalid text type: %s", type(text).__name__)
            raise TypeError("text must be a string")

        if not text.strip():
            logger.error("Empty text received for embedding generation.")
            raise ValueError("text must not be empty")

    def _validate_texts(self, texts: list[str]) -> None:
        """Validate a list of text inputs.

        Args:
            texts: Text values to validate.

        Raises:
            TypeError: If texts is not a list or contains non-string values.
            ValueError: If texts is empty or contains empty text values.
        """
        if not isinstance(texts, list):
            logger.error("Invalid texts type: %s", type(texts).__name__)
            raise TypeError("texts must be a list of strings")

        if not texts:
            logger.error("Empty text list received for embedding generation.")
            raise ValueError("texts must not be empty")

        for index, text in enumerate(texts):
            if not isinstance(text, str):
                logger.error(
                    "Invalid text type at index %s: %s",
                    index,
                    type(text).__name__,
                )
                raise TypeError("all texts must be strings")

            if not text.strip():
                logger.error("Empty text received at index %s.", index)
                raise ValueError("texts must not contain empty values")

    def _to_float_list(self, embedding: Any) -> list[float]:
        """Convert a model embedding output into a standard Python float list.

        Args:
            embedding: Embedding returned by Sentence Transformers.

        Returns:
            Embedding as a Python list of floats.
        """
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        return [float(value) for value in embedding]

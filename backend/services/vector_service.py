import logging
from typing import Any
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.http import models

from backend.core.config import settings


logger = logging.getLogger(__name__)


class VectorService:
    """Service responsible for storing and searching embeddings in Qdrant."""

    def __init__(
        self,
        client: QdrantClient | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
    ) -> None:
        """Initialize the vector service and ensure the collection exists.

        Args:
            client: Optional Qdrant client, useful for tests or dependency injection.
            collection_name: Optional Qdrant collection name.
            vector_size: Optional embedding vector size.
        """
        self.collection_name = collection_name or settings.QDRANT_COLLECTION_NAME
        self.vector_size = vector_size or settings.EMBEDDING_VECTOR_SIZE
        self.client = client or QdrantClient(
            url=self._build_qdrant_url(),
        )

        self.create_collection()

    def _build_qdrant_url(self) -> str:
        """Build a Qdrant URL from settings, accepting URLs with or without a port."""
        qdrant_url = settings.QDRANT_URL.rstrip("/")
        if qdrant_url.rsplit(":", 1)[-1].isdigit():
            return qdrant_url
        return f"{qdrant_url}:{settings.QDRANT_PORT}"

    def create_collection(self) -> None:
        """Create the Qdrant collection if it does not already exist.

        Raises:
            RuntimeError: If Qdrant collection creation or lookup fails.
        """
        try:
            if self.client.collection_exists(collection_name=self.collection_name):
                logger.info("Qdrant collection already exists: %s", self.collection_name)
                return

            logger.info("Creating Qdrant collection: %s", self.collection_name)
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info("Qdrant collection created: %s", self.collection_name)
        except Exception as exc:
            logger.exception("Failed to create or verify Qdrant collection.")
            raise RuntimeError("Failed to create or verify Qdrant collection") from exc

    def upsert_chunks(
        self,
        scenario_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        display_chunks: list[str] | None = None,
        chunk_metadata: list[dict[str, Any]] | None = None,
    ) -> None:
        """Insert or update chunk embeddings in Qdrant.

        Args:
            scenario_id: Identifier of the scenario owning the chunks.
            chunks: Text chunks used for embedding (normalized form).
            embeddings: Embedding vectors matching each chunk.
            display_chunks: Optional accent-preserving versions of each chunk,
                stored next to the normalized text so reports can render a
                readable extract without affecting similarity calculations.

        Raises:
            ValueError: If inputs are empty, inconsistent, or invalid.
            RuntimeError: If Qdrant upsert fails.
        """
        self._validate_upsert_inputs(scenario_id, chunks, embeddings)

        if display_chunks is not None and len(display_chunks) != len(chunks):
            logger.warning(
                "display_chunks length (%s) does not match chunks (%s); "
                "ignoring display chunks.",
                len(display_chunks),
                len(chunks),
            )
            display_chunks = None
        if chunk_metadata is not None and len(chunk_metadata) != len(chunks):
            logger.warning(
                "chunk_metadata length (%s) does not match chunks (%s); "
                "ignoring metadata.",
                len(chunk_metadata),
                len(chunks),
            )
            chunk_metadata = None

        try:
            def _payload(index: int, chunk: str) -> dict[str, Any]:
                metadata = chunk_metadata[index] if chunk_metadata is not None else {}
                payload = {
                    "scenario_id": scenario_id,
                    "chunk_id": metadata.get("chunk_id") or f"{scenario_id}_{index}",
                    "chunk_text": chunk,
                    "chunk_index": metadata.get("chunk_index", index),
                    "page_number": metadata.get("page_number"),
                    "start_offset": metadata.get("start_offset"),
                    "end_offset": metadata.get("end_offset"),
                    "word_count": metadata.get("word_count"),
                    "boilerplate_ratio": metadata.get("boilerplate_ratio", 0.0),
                }
                if display_chunks is not None:
                    payload["chunk_text_display"] = display_chunks[index]
                elif metadata.get("text_display"):
                    payload["chunk_text_display"] = metadata["text_display"]
                return payload

            points = [
                models.PointStruct(
                    id=str(uuid4()),
                    vector=embedding,
                    payload=_payload(index, chunk),
                )
                for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))
            ]

            logger.info(
                "Upserting %s chunk vectors for scenario_id=%s.",
                len(points),
                scenario_id,
            )
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
            logger.info("Chunk vectors upserted for scenario_id=%s.", scenario_id)
        except Exception as exc:
            logger.exception("Failed to upsert chunk vectors for scenario_id=%s.", scenario_id)
            raise RuntimeError("Failed to upsert chunk vectors") from exc

    def search_similar_chunks(
        self,
        embedding: list[float],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for chunks similar to a given embedding.

        Args:
            embedding: Query embedding vector.
            limit: Maximum number of similar chunks to return.

        Returns:
            A list of dictionaries containing point id, score, and payload.

        Raises:
            ValueError: If embedding or limit is invalid.
            RuntimeError: If Qdrant search fails.
        """
        self._validate_search_inputs(embedding, limit)

        try:
            logger.info("Searching similar chunks with limit=%s.", limit)
            points = self._query_points(embedding=embedding, limit=limit)

            return [
                {
                    "id": str(point.id),
                    "score": float(point.score),
                    "payload": dict(point.payload or {}),
                }
                for point in points
            ]
        except Exception as exc:
            logger.exception("Failed to search similar chunks.")
            raise RuntimeError("Failed to search similar chunks") from exc

    def _query_points(self, embedding: list[float], limit: int) -> list[Any]:
        """Search Qdrant with compatibility for recent and older client APIs."""
        query_points = getattr(self.client, "query_points", None)
        if callable(query_points):
            results = query_points(
                collection_name=self.collection_name,
                query=embedding,
                limit=limit,
                with_payload=True,
            )
            points = getattr(results, "points", None)
            try:
                return list(points)
            except TypeError:
                logger.debug("Qdrant query_points returned no iterable points.")

        search = getattr(self.client, "search", None)
        if callable(search):
            return list(
                search(
                    collection_name=self.collection_name,
                    query_vector=embedding,
                    limit=limit,
                    with_payload=True,
                )
            )

        raise RuntimeError("Qdrant client does not expose a supported search method")

    def delete_scenario_vectors(self, scenario_id: str) -> None:
        """Delete all vectors associated with a scenario.

        Args:
            scenario_id: Identifier of the scenario whose vectors should be deleted.

        Raises:
            ValueError: If scenario_id is empty.
            RuntimeError: If Qdrant deletion fails.
        """
        if not scenario_id or not scenario_id.strip():
            logger.error("Empty scenario_id received for vector deletion.")
            raise ValueError("scenario_id must not be empty")

        try:
            logger.info("Deleting vectors for scenario_id=%s.", scenario_id)
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="scenario_id",
                                match=models.MatchValue(value=scenario_id),
                            )
                        ]
                    )
                ),
            )
            logger.info("Vectors deleted for scenario_id=%s.", scenario_id)
        except Exception as exc:
            logger.exception("Failed to delete vectors for scenario_id=%s.", scenario_id)
            raise RuntimeError("Failed to delete scenario vectors") from exc

    def _validate_upsert_inputs(
        self,
        scenario_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> None:
        """Validate inputs used for vector upsert."""
        if not scenario_id or not scenario_id.strip():
            raise ValueError("scenario_id must not be empty")

        if not chunks:
            raise ValueError("chunks must not be empty")

        if not embeddings:
            raise ValueError("embeddings must not be empty")

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")

        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, str) or not chunk.strip():
                raise ValueError(f"chunk at index {index} must not be empty")

        for index, embedding in enumerate(embeddings):
            self._validate_embedding(embedding, f"embedding at index {index}")

    def _validate_search_inputs(self, embedding: list[float], limit: int) -> None:
        """Validate inputs used for vector search."""
        self._validate_embedding(embedding, "embedding")

        if limit <= 0:
            raise ValueError("limit must be greater than 0")

    def _validate_embedding(self, embedding: list[float], field_name: str) -> None:
        """Validate that an embedding is a non-empty numeric vector."""
        if not isinstance(embedding, list) or not embedding:
            raise ValueError(f"{field_name} must be a non-empty list")

        for value in embedding:
            if not isinstance(value, int | float):
                raise ValueError(f"{field_name} must contain only numbers")

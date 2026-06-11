import os
import sys
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402


VECTOR_SIZE = settings.EMBEDDING_VECTOR_SIZE


def build_qdrant_url() -> str:
    qdrant_url = os.getenv("QDRANT_URL", settings.QDRANT_URL).rstrip("/")
    qdrant_port = int(os.getenv("QDRANT_PORT", str(settings.QDRANT_PORT)))

    if qdrant_url.rsplit(":", 1)[-1].isdigit():
        return qdrant_url

    return f"{qdrant_url}:{qdrant_port}"


def main() -> int:
    collection_name = settings.QDRANT_COLLECTION_NAME
    client = QdrantClient(url=build_qdrant_url())

    if client.collection_exists(collection_name=collection_name):
        client.delete_collection(collection_name=collection_name)
        print(f"Collection supprimee: {collection_name}")

    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=VECTOR_SIZE,
            distance=models.Distance.COSINE,
        ),
    )

    print(
        "Succes: collection Qdrant reinitialisee "
        f"({collection_name}, dimension={VECTOR_SIZE}, distance=Cosine)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

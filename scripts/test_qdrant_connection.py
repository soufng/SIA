import os
import sys

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models


QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
TEST_COLLECTION_NAME = os.getenv(
    "QDRANT_TEST_COLLECTION_NAME",
    "test_qdrant_connection",
)
VECTOR_SIZE = int(os.getenv("QDRANT_VECTOR_SIZE", "384"))


def main() -> int:
    load_dotenv()

    qdrant_url = os.getenv("QDRANT_URL", QDRANT_URL)
    collection_name = os.getenv("QDRANT_TEST_COLLECTION_NAME", TEST_COLLECTION_NAME)
    vector_size = int(os.getenv("QDRANT_VECTOR_SIZE", str(VECTOR_SIZE)))

    print(f"Connexion a Qdrant: {qdrant_url}")
    client = QdrantClient(url=qdrant_url)

    try:
        collections = client.get_collections()
        print("Serveur Qdrant joignable.")
        print(f"Collections existantes: {len(collections.collections)}")

        if client.collection_exists(collection_name=collection_name):
            print(f"Collection de test deja existante: {collection_name}")
        else:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            print(f"Collection de test creee: {collection_name}")

        collection_info = client.get_collection(collection_name=collection_name)
        print(f"Statut collection: {collection_info.status}")
        print("Succes: connexion Qdrant fonctionnelle.")
        return 0
    except Exception as exc:
        print(f"Echec: impossible de communiquer avec Qdrant ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

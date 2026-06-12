import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError


DEFAULT_MONGODB_URL = "mongodb://127.0.0.1:27017/sia"
DEFAULT_MONGODB_DATABASE = "sia"
TEST_COLLECTION_NAME = "test_mongodb_connection"


def get_mongodb_url() -> str:
    return (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or DEFAULT_MONGODB_URL
    )


def get_mongodb_database() -> str:
    return (
        os.getenv("MONGO_DB_NAME")
        or os.getenv("MONGODB_DATABASE")
        or os.getenv("MONGODB_DB")
        or DEFAULT_MONGODB_DATABASE
    )


def main() -> int:
    load_dotenv()

    mongodb_url = get_mongodb_url()
    database_name = get_mongodb_database()
    document_id = f"mongodb-connection-test-{uuid4()}"

    print(f"Connexion a MongoDB: {mongodb_url}")
    print(f"Base utilisee: {database_name}")

    client: MongoClient = MongoClient(mongodb_url, serverSelectionTimeoutMS=5000)

    try:
        client.admin.command("ping")

        database = client[database_name]
        collection = database[TEST_COLLECTION_NAME]

        document = {
            "_id": document_id,
            "type": "connection_test",
            "created_at": datetime.now(UTC),
            "message": "MongoDB connection test",
        }

        collection.insert_one(document)

        stored_document = collection.find_one({"_id": document_id})
        if stored_document is None:
            raise RuntimeError("Le document de test n'a pas ete retrouve.")

        collection.delete_one({"_id": document_id})

        print(f"Document insere, relu puis supprime: {document_id}")
        print("Succes: connexion MongoDB fonctionnelle.")
        return 0
    except (PyMongoError, RuntimeError) as exc:
        print(
            f"Echec: impossible de valider la connexion MongoDB ({exc})",
            file=sys.stderr,
        )
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

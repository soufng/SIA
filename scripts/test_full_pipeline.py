import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from qdrant_client import QdrantClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402
from backend.services.adult_content_service import AdultContentService  # noqa: E402
from backend.services.chunking_service import ChunkingService  # noqa: E402
from backend.services.embedding_service import EmbeddingService  # noqa: E402
from backend.services.pdf_service import PDFService  # noqa: E402
from backend.services.plagiarism_service import PlagiarismService  # noqa: E402
from backend.services.profanity_service import ProfanityService  # noqa: E402
from backend.services.template_report_service import TemplateReportService  # noqa: E402
from backend.services.text_cleaning_service import TextCleaningService  # noqa: E402
from backend.services.vector_service import VectorService  # noqa: E402


PDF_PATH = PROJECT_ROOT / "data" / "raw" / "test_scenario.pdf"
RESULTS_COLLECTION_NAME = "analyses"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50


def build_qdrant_url() -> str:
    qdrant_url = os.getenv("QDRANT_URL", settings.QDRANT_URL).rstrip("/")
    qdrant_port = int(os.getenv("QDRANT_PORT", str(settings.QDRANT_PORT)))

    if qdrant_url.rsplit(":", 1)[-1].isdigit():
        return qdrant_url

    return f"{qdrant_url}:{qdrant_port}"


def build_document_stats(text: str, chunks: list[str]) -> dict[str, int]:
    return {
        "word_count": len(text.split()),
        "character_count": len(text),
        "chunk_count": len(chunks),
    }


def save_result_to_mongodb(result: dict[str, Any]) -> str:
    mongodb_url = os.getenv("MONGODB_URL", settings.MONGODB_URL)
    mongodb_database = (
        os.getenv("MONGO_DB_NAME")
        or os.getenv("MONGODB_DATABASE")
        or settings.MONGO_DB_NAME
    )
    result = dict(result)

    if not result.get("analysis_timestamp"):
        result["analysis_timestamp"] = datetime.now(UTC).isoformat()
    if not result.get("created_at"):
        result["created_at"] = result["analysis_timestamp"]
    if not result.get("status"):
        result["status"] = "completed"

    client: MongoClient = MongoClient(mongodb_url, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        client[mongodb_database][RESULTS_COLLECTION_NAME].insert_one(result)
        return mongodb_database
    finally:
        client.close()


def print_results(
    document_stats: dict[str, int],
    embedding_dimension: int,
    plagiarism_result: dict[str, Any],
    profanity_result: dict[str, Any],
    adult_content_result: dict[str, Any],
    rag_report: dict[str, Any],
    execution_time: float,
) -> None:
    print("\n=== Resultats du pipeline complet ===")
    print(f"Nombre de mots: {document_stats['word_count']}")
    print(f"Nombre de chunks: {document_stats['chunk_count']}")
    print(f"Dimension des embeddings: {embedding_dimension}")
    print(
        "Score de similarite: "
        f"{plagiarism_result.get('global_similarity_score', 0.0)}"
    )
    print(f"Score de vulgarite: {profanity_result.get('profanity_score', 0.0)}")
    print(
        "Score contenu adulte: "
        f"{adult_content_result.get('adult_content_score', 0.0)}"
    )
    print(f"Niveau de risque: {rag_report.get('risk_level', 'unknown')}")
    print(f"Resume du rapport RAG: {rag_report.get('summary', '')}")
    print(f"Temps total d'execution: {execution_time:.2f}s")


def run_pipeline() -> int:
    load_dotenv()
    start_time = time.perf_counter()
    scenario_id = f"scenario-{uuid4()}"

    if not PDF_PATH.exists():
        raise FileNotFoundError(
            f"PDF introuvable: {PDF_PATH}. Placez un fichier PDF reel a cet emplacement."
        )

    print("=== Demarrage du pipeline complet ===")
    print(f"PDF: {PDF_PATH}")
    print(f"Scenario ID: {scenario_id}")

    pdf_service = PDFService()
    text_cleaning_service = TextCleaningService()
    chunking_service = ChunkingService()
    embedding_service = EmbeddingService(model_name=settings.EMBEDDING_MODEL_NAME)

    raw_text = pdf_service.extract_text(str(PDF_PATH))
    cleaned_text = text_cleaning_service.clean_text(raw_text)
    chunks = chunking_service.chunk_text(
        cleaned_text,
        chunk_size=CHUNK_SIZE,
        overlap=CHUNK_OVERLAP,
    )

    if not chunks:
        raise ValueError("Aucun chunk genere depuis le PDF. Le texte extrait est vide.")

    embeddings = embedding_service.generate_embeddings(chunks)
    embedding_dimension = len(embeddings[0])

    vector_service = VectorService(
        client=QdrantClient(url=build_qdrant_url()),
        collection_name=settings.QDRANT_COLLECTION_NAME,
        vector_size=embedding_dimension,
    )
    plagiarism_service = PlagiarismService(
        embedding_service=embedding_service,
        vector_service=vector_service,
    )
    profanity_service = ProfanityService()
    adult_content_service = AdultContentService()
    template_report_service = TemplateReportService()

    plagiarism_result = plagiarism_service.analyze_chunks(
        scenario_id=scenario_id,
        chunks=chunks,
    )
    profanity_result = profanity_service.analyze_text(cleaned_text)
    adult_content_result = adult_content_service.analyze_text(cleaned_text)
    document_stats = build_document_stats(cleaned_text, chunks)

    rag_report = template_report_service.generate_report(
        scenario_id=scenario_id,
        plagiarism_result=plagiarism_result,
        profanity_result=profanity_result,
        adult_content_result=adult_content_result,
        document_stats=document_stats,
    )

    vector_service.upsert_chunks(
        scenario_id=scenario_id,
        chunks=chunks,
        embeddings=embeddings,
    )

    execution_time = time.perf_counter() - start_time
    final_result = {
        "_id": f"full-pipeline-{scenario_id}",
        "filename": PDF_PATH.name,
        "score": plagiarism_result.get("global_similarity_score", 0.0),
        "status": "completed",
        "scenario_id": scenario_id,
        "pdf_path": str(PDF_PATH),
        "document_stats": document_stats,
        "embedding_dimension": embedding_dimension,
        "plagiarism_result": plagiarism_result,
        "profanity_result": profanity_result,
        "adult_content_result": adult_content_result,
        "rag_report": rag_report,
        "execution_time_seconds": round(execution_time, 2),
    }
    final_result["result"] = {
        "scenario_id": scenario_id,
        "document_stats": document_stats,
        "plagiarism": plagiarism_result,
        "profanity": profanity_result,
        "adult_content": adult_content_result,
        "rag_report": rag_report,
        "execution_time_seconds": round(execution_time, 2),
    }

    mongodb_database = save_result_to_mongodb(final_result)
    print(f"Resultat sauvegarde dans MongoDB: {mongodb_database}.{RESULTS_COLLECTION_NAME}")

    print_results(
        document_stats=document_stats,
        embedding_dimension=embedding_dimension,
        plagiarism_result=plagiarism_result,
        profanity_result=profanity_result,
        adult_content_result=adult_content_result,
        rag_report=rag_report,
        execution_time=execution_time,
    )
    return 0


def main() -> int:
    try:
        return run_pipeline()
    except FileNotFoundError as exc:
        print(f"Erreur fichier: {exc}", file=sys.stderr)
    except ValueError as exc:
        print(f"Erreur donnees: {exc}", file=sys.stderr)
    except PyMongoError as exc:
        print(f"Erreur MongoDB: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Erreur pipeline: {exc}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

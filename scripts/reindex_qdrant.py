"""Re-index every saved analysis into Qdrant.

Use this script after switching ``SPM_EMBEDDING_MODEL`` (and the matching
``SPM_EMBEDDING_VECTOR_SIZE``). Workflow:

    python scripts/reset_qdrant_collection.py   # drop + recreate with new size
    python scripts/reindex_qdrant.py            # re-embed and re-upsert

Each MongoDB history document is replayed: the stored PDF is re-extracted
and re-chunked exactly the same way as ``AnalysisService.analyze_scenario``
does at upload time, then the chunks are embedded with the *current*
embedding model and pushed to Qdrant under the original ``scenario_id``.

The script is idempotent — running it twice with the same model produces
the same vectors. Missing PDFs are skipped with a warning.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402
from backend.repositories.analysis_repository import AnalysisRepository  # noqa: E402
from backend.services.chunking_service import ChunkingService  # noqa: E402
from backend.services.embedding_service import EmbeddingService  # noqa: E402
from backend.services.pdf_service import PDFService  # noqa: E402
from backend.services.text_cleaning_service import TextCleaningService  # noqa: E402
from backend.services.vector_service import VectorService  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reindex_qdrant")


UPLOAD_DIR = Path("data/raw")


def _resolve_pdf_path(history_doc: dict[str, Any]) -> Path | None:
    stored = (
        history_doc.get("stored_filename")
        or history_doc.get("document_stats", {}).get("file_name")
        or ""
    )
    if not stored:
        return None
    candidate = UPLOAD_DIR / str(stored)
    return candidate if candidate.is_file() else None


def _replay_chunks(
    pdf_path: Path,
    pdf_service: PDFService,
    cleaning: TextCleaningService,
    chunking: ChunkingService,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    raw_text = pdf_service.extract_text(str(pdf_path))
    cleaned = cleaning.clean_text(raw_text)
    display_text = cleaning.clean_text(raw_text)
    pairs = chunking.chunk_text_with_display(
        text_normalized=cleaned,
        text_display=display_text,
        chunk_size=settings.PLAGIARISM_CHUNK_SIZE,
        overlap=settings.PLAGIARISM_CHUNK_OVERLAP,
    )
    chunks = [pair[0] for pair in pairs]
    display_chunks = [pair[1] for pair in pairs]
    metadata = [
        {
            "chunk_id": f"chunk_{idx}",
            "chunk_index": idx,
            "page_number": None,
            "start_offset": None,
            "end_offset": None,
            "text_normalized": normalized,
            "text_display": display,
            "raw_text": display,
            "word_count": len(normalized.split()),
        }
        for idx, (normalized, display) in enumerate(pairs)
    ]
    return chunks, display_chunks, metadata


def main() -> int:
    repo = AnalysisRepository()
    embedding = EmbeddingService()
    vector = VectorService()
    pdf_service = PDFService()
    cleaning = TextCleaningService()
    chunking = ChunkingService()

    history = repo.list_history(limit=10_000)
    logger.info("Re-indexing %s analyses into Qdrant.", len(history))

    skipped: list[str] = []
    reindexed = 0

    for doc in history:
        scenario_id = str(doc.get("scenario_id") or "").strip()
        if not scenario_id:
            skipped.append("(no scenario_id)")
            continue

        pdf_path = _resolve_pdf_path(doc)
        if pdf_path is None:
            logger.warning(
                "Skipping scenario_id=%s: source PDF is no longer available.",
                scenario_id,
            )
            skipped.append(scenario_id)
            continue

        try:
            chunks, display_chunks, metadata = _replay_chunks(
                pdf_path=pdf_path,
                pdf_service=pdf_service,
                cleaning=cleaning,
                chunking=chunking,
            )
        except Exception:
            logger.exception(
                "Skipping scenario_id=%s: failed to re-chunk %s.",
                scenario_id,
                pdf_path,
            )
            skipped.append(scenario_id)
            continue

        if not chunks:
            logger.warning(
                "Skipping scenario_id=%s: no chunks produced.", scenario_id
            )
            skipped.append(scenario_id)
            continue

        try:
            embeddings = embedding.generate_embeddings(chunks)
            # Wipe any stale vectors for this scenario before inserting
            # so we don't accumulate duplicates across runs.
            vector.delete_scenario_vectors(scenario_id)
            vector.upsert_chunks(
                scenario_id=scenario_id,
                chunks=chunks,
                embeddings=embeddings,
                display_chunks=display_chunks,
                chunk_metadata=metadata,
            )
        except Exception:
            logger.exception(
                "Skipping scenario_id=%s: embedding/upsert failed.", scenario_id
            )
            skipped.append(scenario_id)
            continue

        reindexed += 1
        logger.info(
            "Re-indexed scenario_id=%s (%s chunks).", scenario_id, len(chunks)
        )

    logger.info(
        "Re-indexing complete. reindexed=%s skipped=%s model=%s vector_size=%s",
        reindexed,
        len(skipped),
        settings.EMBEDDING_MODEL_NAME,
        settings.EMBEDDING_VECTOR_SIZE,
    )
    if skipped:
        logger.info("Skipped scenarios: %s", ", ".join(skipped[:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Persistance des jobs d'analyse asynchrones dans MongoDB.

Une seule collection ``analysis_jobs`` : chaque ligne représente un
upload qui a été accepté par l'API et dont l'analyse tourne (ou a tourné)
en arrière-plan. Le frontend poll ``GET /uploads/jobs/{id}`` pour suivre
la progression.

Les statuts possibles :

- ``queued``    : enregistré, pas encore démarré
- ``running``   : pipeline en cours, ``stage`` indique l'étape
- ``completed`` : terminé, ``result_scenario_id`` pointe sur l'analyse
- ``failed``    : échec, ``error`` contient le message
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo.database import Database

from backend.core.config import settings
from backend.db.mongodb import get_database


logger = logging.getLogger(__name__)

JOBS_COLLECTION_NAME = "analysis_jobs"


JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"


class JobsRepository:
    """Read/write the asynchronous job state in MongoDB."""

    def __init__(
        self,
        mongodb_url: str | None = None,
        database_name: str | None = None,
        collection_name: str = JOBS_COLLECTION_NAME,
        database: Database | None = None,
    ) -> None:
        self.mongodb_url = mongodb_url or settings.MONGODB_URL
        self.database_name = database_name or settings.MONGO_DB_NAME
        self.collection_name = collection_name
        self.database = database

    # ---------- Public API ----------

    def create_job(
        self,
        *,
        file_path: str,
        original_filename: str,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a fresh job row and return its serialised representation.

        ``scenario_id`` is generated up-front so the operator can be told
        what id they should look for once the job completes.
        """
        now = _utcnow_iso()
        document = {
            "job_id": str(uuid4()),
            "scenario_id": scenario_id or str(uuid4()),
            "status": JOB_STATUS_QUEUED,
            "stage": "queued",
            "progress_pct": 0,
            "original_filename": original_filename,
            "file_path": file_path,
            "result_scenario_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        collection = self._collection()
        collection.insert_one(document)
        logger.info(
            "JobsRepository: created job_id=%s scenario_id=%s",
            document["job_id"],
            document["scenario_id"],
        )
        return _serialize(document)

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress_pct: int | None = None,
        result_scenario_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Patch a job row. All fields are optional except ``job_id``."""
        if not job_id:
            raise ValueError("job_id must not be empty")

        updates: dict[str, Any] = {"updated_at": _utcnow_iso()}
        if status is not None:
            updates["status"] = status
        if stage is not None:
            updates["stage"] = stage
        if progress_pct is not None:
            updates["progress_pct"] = max(0, min(100, int(progress_pct)))
        if result_scenario_id is not None:
            updates["result_scenario_id"] = result_scenario_id
        if error is not None:
            updates["error"] = error

        collection = self._collection()
        result = collection.update_one({"job_id": job_id}, {"$set": updates})
        if result.matched_count == 0:
            logger.warning(
                "JobsRepository: update_job called for missing job_id=%s", job_id
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Return the job state, or ``None`` if no such row exists."""
        if not job_id:
            return None
        collection = self._collection()
        document = collection.find_one({"job_id": job_id})
        return _serialize(document) if document else None

    # ---------- Internals ----------

    def _collection(self):
        return self._get_database()[self.collection_name]

    def _get_database(self) -> Database:
        if self.database is not None:
            return self.database
        return get_database()


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize(document: dict[str, Any]) -> dict[str, Any]:
    """Drop Mongo's ``_id`` and keep everything else serialisable."""
    out = dict(document)
    out.pop("_id", None)
    return out

"""Exécute un job d'analyse en arrière-plan et tient le repository à jour.

Pas de Redis/ARQ pour le moment : on s'appuie sur ``BackgroundTasks`` de
FastAPI qui exécute le callable *après* avoir renvoyé la réponse HTTP.
Tant qu'on est en mono-instance, ça suffit largement et ça permet de :

- répondre 202 ``{job_id, status: queued}`` immédiatement,
- éviter le timeout HTTP des reverse proxies sur les longs PDF,
- offrir au frontend un endpoint de polling avec une vraie progression.

Si plus tard il faut du multi-worker / des retries, la fonction
``run_analysis_job`` reste exactement la même : il suffit de la décorer
en tâche ARQ et de la déposer dans une queue Redis. Le contrat de
``JobsRepository`` est conçu pour ce switch.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.repositories.analysis_repository import AnalysisRepository
from backend.repositories.jobs_repository import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JobsRepository,
)
from backend.services.analysis_service import AnalysisService


logger = logging.getLogger(__name__)


# Étapes affichées au frontend. Le pourcentage est un repère visuel, pas
# une mesure précise — l'analyse synchrone reste un seul appel à
# ``analyze_scenario`` qu'on ne peut pas découper sans changer l'API
# interne. On marque le démarrage et la fin ; le frontend interpole
# visuellement entre les deux comme avant.
_STAGE_START = ("Lancement de l'analyse", 5)
_STAGE_PIPELINE = ("Exécution du pipeline complet", 40)
_STAGE_PERSIST = ("Enregistrement de l'analyse", 90)
_STAGE_DONE = ("Terminé", 100)


def run_analysis_job(
    *,
    job_id: str,
    scenario_id: str,
    file_path: str,
    original_filename: str,
    analysis_service: AnalysisService,
    analysis_repository: AnalysisRepository,
    jobs_repository: JobsRepository,
    history_document_builder,
) -> None:
    """Exécute le pipeline complet et tient ``jobs_repository`` à jour.

    Toute exception est attrapée — un job qui plante doit se solder par
    un statut ``failed`` propre, jamais par une exception qui remonte
    dans le thread pool de FastAPI.
    """
    try:
        jobs_repository.update_job(
            job_id,
            status=JOB_STATUS_RUNNING,
            stage=_STAGE_START[0],
            progress_pct=_STAGE_START[1],
        )

        jobs_repository.update_job(
            job_id,
            stage=_STAGE_PIPELINE[0],
            progress_pct=_STAGE_PIPELINE[1],
        )
        analysis_result = analysis_service.analyze_scenario(
            scenario_id=scenario_id,
            file_path=file_path,
            original_filename=original_filename,
        )

        jobs_repository.update_job(
            job_id,
            stage=_STAGE_PERSIST[0],
            progress_pct=_STAGE_PERSIST[1],
        )
        analysis_repository.save_result(history_document_builder(analysis_result))

        jobs_repository.update_job(
            job_id,
            status=JOB_STATUS_COMPLETED,
            stage=_STAGE_DONE[0],
            progress_pct=_STAGE_DONE[1],
            result_scenario_id=analysis_result.get("scenario_id") or scenario_id,
        )
        logger.info(
            "JobRunner: job_id=%s completed scenario_id=%s",
            job_id,
            scenario_id,
        )
    except Exception as exc:  # noqa: BLE001 - we *want* the broadest net here
        message = _root_error_message(exc)
        logger.exception(
            "JobRunner: job_id=%s failed: %s",
            job_id,
            message,
        )
        try:
            jobs_repository.update_job(
                job_id,
                status=JOB_STATUS_FAILED,
                stage="Échec",
                error=message,
            )
        except Exception:  # pragma: no cover - if Mongo is down too, log it
            logger.exception(
                "JobRunner: failed to persist failure state for job_id=%s",
                job_id,
            )


def _root_error_message(exc: BaseException) -> str:
    current: BaseException = exc
    while current.__cause__ is not None:
        current = current.__cause__
    return str(current) or current.__class__.__name__


# Re-export for callers that want to type-hint the result.
__all__ = ["run_analysis_job"]


# Type alias pour le builder de document MongoDB injecté depuis la route.
HistoryDocumentBuilder = Any

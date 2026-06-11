import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from backend.api.v1.dependencies import require_user
from backend.repositories.analysis_repository import AnalysisRepository
from backend.services.advanced_rag_service import AdvancedRAGService


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis", tags=["analysis"], dependencies=[Depends(require_user)]
)


def get_analysis_repository() -> AnalysisRepository:
    """Return an AnalysisRepository instance for dependency injection."""
    return AnalysisRepository()


def get_advanced_rag_service() -> AdvancedRAGService:
    """Return an AdvancedRAGService instance for dependency injection."""
    return AdvancedRAGService()


class AdvancedReportBodyRequest(BaseModel):
    """Optional body for the advanced-report endpoint.

    Callers can either reference a scenario_id (the analysis is looked up in
    MongoDB) or pass the full ``analysis`` payload directly — useful right
    after an upload, before the user has navigated away.
    """

    analysis: dict[str, Any] | None = None


@router.get("/history")
def get_analysis_history(
    limit: int = Query(default=20, ge=1, le=100),
    repository: AnalysisRepository = Depends(get_analysis_repository),
) -> dict[str, list[dict[str, Any]]]:
    """Return saved analysis results from MongoDB."""
    try:
        logger.info("Fetching analysis history. limit=%s", limit)
        items = repository.list_history(limit=limit)
        return {"items": items}
    except PyMongoError as exc:
        logger.exception("MongoDB error while fetching analysis history.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Impossible de lire l'historique depuis MongoDB.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error while fetching analysis history.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors du chargement de l'historique.",
        ) from exc


@router.post("/{scenario_id}/advanced-report")
def generate_advanced_report(
    scenario_id: str,
    payload: AdvancedReportBodyRequest = Body(default=AdvancedReportBodyRequest()),
    repository: AnalysisRepository = Depends(get_analysis_repository),
    service: AdvancedRAGService = Depends(get_advanced_rag_service),
) -> dict[str, Any]:
    """Generate an explanatory RAG report for an existing analysis.

    Workflow: try the body-provided ``analysis`` payload first (fast path,
    no DB roundtrip). Otherwise look up the analysis in MongoDB by
    ``scenario_id``.
    """
    if not scenario_id or not scenario_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scenario_id must not be empty",
        )

    analysis: dict[str, Any] | None = payload.analysis if payload else None
    if analysis is None:
        try:
            logger.info(
                "Loading analysis %s from MongoDB for advanced RAG report.",
                scenario_id,
            )
            history = repository.list_history(limit=200)
            analysis = next(
                (
                    doc.get("result") or doc.get("analysis") or doc
                    for doc in history
                    if str(doc.get("scenario_id") or "") == scenario_id
                ),
                None,
            )
        except PyMongoError as exc:
            logger.exception(
                "MongoDB error while loading analysis for advanced report."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Impossible de lire l'analyse depuis MongoDB.",
            ) from exc

    if not isinstance(analysis, dict) or not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analyse introuvable pour scenario_id={scenario_id}.",
        )

    try:
        report = service.generate(analysis=analysis, scenario_id=scenario_id)
        return report
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.exception("Advanced RAG generation failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport explicatif RAG.",
        ) from exc


@router.get("/statistics")
def get_analysis_statistics(
    repository: AnalysisRepository = Depends(get_analysis_repository),
) -> dict[str, Any]:
    """Return aggregate analysis statistics computed from MongoDB."""
    try:
        logger.info("Fetching analysis statistics.")
        return repository.get_statistics()
    except PyMongoError as exc:
        logger.exception("MongoDB error while fetching analysis statistics.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Impossible de calculer les statistiques depuis MongoDB.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error while fetching analysis statistics.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne lors du calcul des statistiques.",
        ) from exc

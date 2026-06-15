import logging
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from backend.api.v1.dependencies import CurrentUser, require_user
from backend.core.config import settings
from backend.core.rate_limit import limiter
from backend.repositories.analysis_repository import AnalysisRepository
from backend.repositories.audit_log_repository import (
    AuditLogRepository,
    EVENT_SCENARIO_UPLOAD,
)
from backend.repositories.jobs_repository import JobsRepository
from backend.services.analysis_service import AnalysisService
from backend.services.job_runner import run_analysis_job
from backend.services.upload_service import UploadService


logger = logging.getLogger(__name__)


# Signature magique du format PDF (RFC 8118 §4). On accepte un en-tête
# en début de fichier OU précédé de jusqu'à 1 KiB de bruit (certains PDF
# malformés ajoutent un préambule).
_PDF_MAGIC = b"%PDF-"
_PDF_MAGIC_SCAN_WINDOW = 1024


def _validate_pdf_bytes(content: bytes, filename: str) -> None:
    """Vérifie la taille et la signature binaire d'un PDF.

    Raise HTTPException 400 si le contenu n'est pas un PDF plausible ou
    dépasse ``settings.UPLOAD_MAX_MB``.
    """
    if not content:
        logger.error("Upload rejected: empty file (%s)", filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier est vide.",
        )

    max_mb = int(settings.UPLOAD_MAX_MB or 0)
    if max_mb > 0 and len(content) > max_mb * 1024 * 1024:
        logger.error(
            "Upload rejected: file too large (%s, %s bytes, max %s Mo)",
            filename,
            len(content),
            max_mb,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Le fichier dépasse la taille maximale autorisée "
                f"({max_mb} Mo)."
            ),
        )

    head = content[:_PDF_MAGIC_SCAN_WINDOW]
    if _PDF_MAGIC not in head:
        logger.error(
            "Upload rejected: PDF magic header not found in first %s bytes "
            "of %s",
            _PDF_MAGIC_SCAN_WINDOW,
            filename,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Le fichier ne ressemble pas à un PDF valide "
                "(en-tête %PDF- introuvable)."
            ),
        )

router = APIRouter(
    prefix="/uploads", tags=["uploads"], dependencies=[Depends(require_user)]
)


def get_upload_service() -> UploadService:
    """Return an UploadService instance for FastAPI dependency injection."""
    return UploadService()


def get_analysis_service() -> AnalysisService:
    """Return an AnalysisService instance for FastAPI dependency injection."""
    return AnalysisService()


def get_analysis_repository() -> AnalysisRepository:
    """Return an AnalysisRepository instance for dependency injection."""
    return AnalysisRepository()


def get_jobs_repository() -> JobsRepository:
    """Return a JobsRepository instance for dependency injection."""
    return JobsRepository()


def get_audit_log_repository() -> AuditLogRepository:
    """Return an AuditLogRepository instance for dependency injection."""
    return AuditLogRepository()


def _client_ip(request: Request) -> str | None:
    fw = request.headers.get("x-forwarded-for")
    if fw:
        return fw.split(",")[0].strip()
    return request.client.host if request.client else None


def _audit_scenario_upload(
    audit: AuditLogRepository,
    actor: CurrentUser | None,
    request: Request,
    *,
    scenario_id: str | None,
    filename: str | None,
    flavour: str,
) -> None:
    try:
        audit.append(
            event_type=EVENT_SCENARIO_UPLOAD,
            user_id=actor.user_id if actor else None,
            username=actor.username if actor else None,
            target_id=scenario_id,
            ip=_client_ip(request),
            details={"filename": filename, "flavour": flavour},
        )
    except Exception:  # pragma: no cover - audit best-effort
        logger.debug("Audit append failed for scenario_upload", exc_info=True)


def _read_and_validate_pdf(file: UploadFile | None) -> bytes:
    """Common pre-flight checks shared by sync and async upload endpoints."""
    if file is None:
        logger.error("Upload rejected: no file provided.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un fichier est requis.",
        )
    if not file.filename:
        logger.error("Upload rejected: missing filename.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le nom du fichier est requis.",
        )
    if not file.filename.lower().endswith(".pdf"):
        logger.error("Upload rejected: non-PDF file received: %s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier uploadé doit être un PDF (extension .pdf).",
        )
    # The actual byte read happens at the call site so endpoints can pass
    # the resulting bytes to whichever storage backend they need.
    return b""


@router.post("/analyze")
@limiter.limit("20/hour")
async def upload_and_analyze(
    request: Request,
    file: UploadFile | None = File(default=None),
    upload_service: UploadService = Depends(get_upload_service),
    analysis_service: AnalysisService = Depends(get_analysis_service),
    analysis_repository: AnalysisRepository = Depends(get_analysis_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
    actor: CurrentUser = Depends(require_user),
) -> dict[str, Any]:
    """Upload a PDF file and run the complete scenario analysis.

    Args:
        file: PDF file received from the client.
        upload_service: Service used to save the uploaded file locally.
        analysis_service: Service used to run the full analysis pipeline.

    Returns:
        Response containing success status, scenario id, and complete analysis.

    Raises:
        HTTPException: If the file is invalid or the upload/analysis fails.
    """
    if file is None:
        logger.error("Upload rejected: no file provided.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un fichier est requis.",
        )

    if not file.filename:
        logger.error("Upload rejected: missing filename.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le nom du fichier est requis.",
        )

    if not file.filename.lower().endswith(".pdf"):
        logger.error("Upload rejected: non-PDF file received: %s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier uploadé doit être un PDF (extension .pdf).",
        )

    logger.info("Reading uploaded PDF file: %s", file.filename)
    try:
        file_content = await file.read()
    finally:
        await file.close()

    # Validation de fond : taille + signature binaire %PDF-.
    # Hors du try/except global pour que les HTTPException remontent
    # telles quelles au lieu d'être converties en 500.
    _validate_pdf_bytes(file_content, file.filename)

    try:
        file_info = upload_service.save_uploaded_file(
            file_content=file_content,
            original_filename=file.filename,
        )
        scenario_id = str(uuid4())

        logger.info(
            "Launching scenario analysis. scenario_id=%s file_path=%s",
            scenario_id,
            file_info["file_path"],
        )
        analysis_result = analysis_service.analyze_scenario(
            scenario_id=scenario_id,
            file_path=str(file_info["file_path"]),
            original_filename=str(file_info["original_filename"]),
        )
        analysis_repository.save_result(_build_history_document(analysis_result))

        _audit_scenario_upload(
            audit,
            actor,
            request,
            scenario_id=scenario_id,
            filename=file.filename,
            flavour="sync",
        )
        logger.info("Upload and analysis completed. scenario_id=%s", scenario_id)
        return {
            "success": True,
            "scenario_id": scenario_id,
            "analysis": analysis_result,
        }
    except ValueError as exc:
        logger.exception("Invalid upload request for file=%s.", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        logger.exception("Uploaded file could not be processed: %s", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except PyMongoError as exc:
        logger.exception(
            "Analysis completed but MongoDB history save failed for file=%s.",
            file.filename,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "Analyse terminee, mais sauvegarde historique MongoDB impossible.",
                "error": str(exc),
            },
        )
    except Exception as exc:
        logger.exception("Upload and analysis failed for file=%s.", file.filename)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Erreur pendant l'analyse du PDF",
                "error": _root_error_message(exc),
            },
        )


@router.post("/analyze/async", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("30/hour")
async def upload_and_queue_analysis(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    upload_service: UploadService = Depends(get_upload_service),
    analysis_service: AnalysisService = Depends(get_analysis_service),
    analysis_repository: AnalysisRepository = Depends(get_analysis_repository),
    jobs_repository: JobsRepository = Depends(get_jobs_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
    actor: CurrentUser = Depends(require_user),
) -> dict[str, Any]:
    """Accept a PDF, persist it, enqueue the analysis and return a job id.

    The actual analysis runs in a FastAPI ``BackgroundTask`` after the
    response is sent, so this endpoint always answers in well under a
    second even for big PDFs. The client polls ``GET /uploads/jobs/{id}``
    to follow the progression and read the resulting ``scenario_id``.
    """
    _read_and_validate_pdf(file)
    assert file is not None and file.filename is not None  # narrows for mypy

    logger.info("Reading uploaded PDF file (async): %s", file.filename)
    try:
        file_content = await file.read()
    finally:
        await file.close()

    _validate_pdf_bytes(file_content, file.filename)

    try:
        file_info = upload_service.save_uploaded_file(
            file_content=file_content,
            original_filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    scenario_id = str(uuid4())
    job = jobs_repository.create_job(
        file_path=str(file_info["file_path"]),
        original_filename=str(file_info["original_filename"]),
        scenario_id=scenario_id,
    )

    background_tasks.add_task(
        run_analysis_job,
        job_id=job["job_id"],
        scenario_id=scenario_id,
        file_path=str(file_info["file_path"]),
        original_filename=str(file_info["original_filename"]),
        analysis_service=analysis_service,
        analysis_repository=analysis_repository,
        jobs_repository=jobs_repository,
        history_document_builder=_build_history_document,
    )

    _audit_scenario_upload(
        audit,
        actor,
        request,
        scenario_id=scenario_id,
        filename=file.filename,
        flavour="async",
    )
    logger.info(
        "Queued analysis job_id=%s scenario_id=%s file=%s",
        job["job_id"],
        scenario_id,
        file.filename,
    )
    return {
        "success": True,
        "job_id": job["job_id"],
        "scenario_id": scenario_id,
        "status": job["status"],
        "stage": job["stage"],
        "progress_pct": job["progress_pct"],
    }


@router.post("/analyze/async/batch", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("15/hour")
async def upload_batch_and_queue_analysis(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    upload_service: UploadService = Depends(get_upload_service),
    analysis_service: AnalysisService = Depends(get_analysis_service),
    analysis_repository: AnalysisRepository = Depends(get_analysis_repository),
    jobs_repository: JobsRepository = Depends(get_jobs_repository),
    audit: AuditLogRepository = Depends(get_audit_log_repository),
    actor: CurrentUser = Depends(require_user),
) -> dict[str, Any]:
    """Accept N PDFs in one request and queue one independent analysis per file.

    Chaque fichier est validé puis traité par la même pipeline asynchrone que
    ``/uploads/analyze/async`` — un ``job_id`` et un ``scenario_id`` distincts
    sont créés pour chaque PDF, et chaque job apparaît séparément dans
    l'historique. Le client suit la progression de chaque job via
    ``GET /uploads/jobs/{id}``.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Au moins un fichier est requis.",
        )
    if len(files) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trop de fichiers (maximum 20 par batch).",
        )

    # Lecture et validation de TOUS les fichiers d'abord — si l'un est
    # invalide, on rejette le batch entier sans rien enqueuer (atomicité).
    pdf_contents: list[tuple[bytes, str]] = []
    for file in files:
        _read_and_validate_pdf(file)
        assert file.filename is not None
        try:
            content = await file.read()
        finally:
            await file.close()
        _validate_pdf_bytes(content, file.filename)
        pdf_contents.append((content, file.filename))

    logger.info(
        "Queuing batch analysis for %d PDF files: %s",
        len(pdf_contents),
        [name for _, name in pdf_contents],
    )

    jobs: list[dict[str, Any]] = []
    for content, filename in pdf_contents:
        try:
            file_info = upload_service.save_uploaded_file(
                file_content=content,
                original_filename=filename,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        scenario_id = str(uuid4())
        job = jobs_repository.create_job(
            file_path=str(file_info["file_path"]),
            original_filename=str(file_info["original_filename"]),
            scenario_id=scenario_id,
        )

        background_tasks.add_task(
            run_analysis_job,
            job_id=job["job_id"],
            scenario_id=scenario_id,
            file_path=str(file_info["file_path"]),
            original_filename=str(file_info["original_filename"]),
            analysis_service=analysis_service,
            analysis_repository=analysis_repository,
            jobs_repository=jobs_repository,
            history_document_builder=_build_history_document,
        )

        _audit_scenario_upload(
            audit,
            actor,
            request,
            scenario_id=scenario_id,
            filename=filename,
            flavour="async-batch",
        )

        jobs.append(
            {
                "success": True,
                "job_id": job["job_id"],
                "scenario_id": scenario_id,
                "status": job["status"],
                "stage": job["stage"],
                "progress_pct": job["progress_pct"],
                "original_filename": filename,
            }
        )

    logger.info(
        "Queued %d batch analysis jobs: %s",
        len(jobs),
        [j["job_id"] for j in jobs],
    )
    return {"success": True, "count": len(jobs), "jobs": jobs}


@router.get("/jobs/{job_id}")
def get_job_state(
    job_id: str,
    jobs_repository: JobsRepository = Depends(get_jobs_repository),
    analysis_repository: AnalysisRepository = Depends(get_analysis_repository),
) -> dict[str, Any]:
    """Return the current state of an analysis job.

    The frontend polls this endpoint to drive its real progress bar. Once
    the job is ``completed``, the response also embeds the full analysis
    payload so the client doesn't need a second round-trip.
    """
    if not job_id or not job_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'identifiant du job est requis.",
        )
    try:
        job = jobs_repository.get_job(job_id)
    except PyMongoError as exc:
        logger.exception("MongoDB error while reading job state.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Impossible de lire l'état du job (MongoDB indisponible).",
        ) from exc
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job introuvable.",
        )

    # Once the analysis is done, attach the full payload so the frontend
    # can render the report from a single response.
    if (
        job.get("status") == "completed"
        and job.get("result_scenario_id")
    ):
        try:
            history_doc = analysis_repository.find_by_scenario_id(
                str(job["result_scenario_id"])
            )
        except PyMongoError as exc:
            logger.exception(
                "MongoDB error while loading completed analysis for job_id=%s",
                job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Impossible de lire l'analyse depuis MongoDB.",
            ) from exc
        if history_doc is not None:
            # The frontend already knows how to consume the "result" field
            # of a history document, so we expose it under the canonical
            # ``analysis`` key here.
            job["analysis"] = history_doc.get("result") or history_doc

    return job


def _build_history_document(analysis_result: dict[str, Any]) -> dict[str, Any]:
    """Keep MongoDB history documents aligned with the history API contract.

    The MongoDB BSON limit is 16 MiB. Long PDFs analysed with a permissive
    threshold can produce ``analysis_result`` payloads of several MiB; we
    therefore avoid duplicating them. Indexed-search/filter fields live at
    the top level (``filename``, ``file_hash``, ``risk_level``, …) and the
    rest of the payload lives once under ``result``.
    """
    document_stats = analysis_result.get("document_stats", {})
    plagiarism = analysis_result.get("plagiarism", {})
    score = 0.0
    if isinstance(plagiarism, dict):
        score = (
            plagiarism.get("score")
            or plagiarism.get("global_similarity_score")
            or 0.0
        )

    return {
        "scenario_id": analysis_result.get("scenario_id", ""),
        "filename": document_stats.get("original_filename")
        or document_stats.get("file_name", ""),
        "stored_filename": document_stats.get("file_name", ""),
        "file_hash": analysis_result.get("file_hash")
        or document_stats.get("file_hash"),
        "text_hash": analysis_result.get("text_hash")
        or document_stats.get("text_hash"),
        "word_count": document_stats.get("words_count", 0),
        "chunk_count": document_stats.get("chunks_count", 0),
        "similarity_score": score,
        "risk_level": analysis_result.get("rag_report", {}).get(
            "risk_level",
            plagiarism.get("risk", "unknown")
            if isinstance(plagiarism, dict)
            else "unknown",
        ),
        "score": score,
        "status": analysis_result.get("status", "completed"),
        "created_at": analysis_result.get("analysis_timestamp"),
        "analysis_timestamp": analysis_result.get("analysis_timestamp"),
        "warnings": analysis_result.get("warnings", []),
        "moroccan_constants": analysis_result.get("moroccan_constants", {}),
        # Full analysis payload — kept exactly once to stay under the
        # 16 MiB BSON limit. Other fields above are denormalised copies of
        # this payload's metadata, useful for list/filter queries.
        "result": analysis_result,
    }


def _root_error_message(exc: BaseException) -> str:
    """Return the deepest useful exception message for the API response."""
    current: BaseException = exc
    while current.__cause__ is not None:
        current = current.__cause__
    return str(current) or current.__class__.__name__

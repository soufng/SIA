"""Tests pour ``run_analysis_job`` : succès, échec, ordre des updates."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from backend.repositories.jobs_repository import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
)
from backend.services.job_runner import run_analysis_job


def _fake_history_builder(analysis: dict[str, Any]) -> dict[str, Any]:
    return {"result": analysis}


def test_run_analysis_job_marks_completed_on_success() -> None:
    analysis_service = Mock()
    analysis_service.analyze_scenario.return_value = {
        "scenario_id": "scenario-final",
        "document_stats": {},
        "plagiarism": {},
    }
    analysis_repository = Mock()
    jobs_repository = Mock()

    run_analysis_job(
        job_id="job-1",
        scenario_id="scenario-final",
        file_path="data/raw/x.pdf",
        original_filename="x.pdf",
        analysis_service=analysis_service,
        analysis_repository=analysis_repository,
        jobs_repository=jobs_repository,
        history_document_builder=_fake_history_builder,
    )

    # Le pipeline a bien été appelé.
    analysis_service.analyze_scenario.assert_called_once_with(
        scenario_id="scenario-final",
        file_path="data/raw/x.pdf",
        original_filename="x.pdf",
    )
    analysis_repository.save_result.assert_called_once()

    # Au moins un update running et un update completed.
    statuses = [
        call.kwargs.get("status")
        for call in jobs_repository.update_job.call_args_list
        if "status" in call.kwargs
    ]
    assert JOB_STATUS_RUNNING in statuses
    assert statuses[-1] == JOB_STATUS_COMPLETED

    # Le dernier update embarque le scenario_id final.
    last_call = jobs_repository.update_job.call_args_list[-1]
    assert last_call.kwargs.get("result_scenario_id") == "scenario-final"
    assert last_call.kwargs.get("progress_pct") == 100


def test_run_analysis_job_marks_failed_when_pipeline_raises() -> None:
    analysis_service = Mock()
    analysis_service.analyze_scenario.side_effect = RuntimeError("boom")
    analysis_repository = Mock()
    jobs_repository = Mock()

    run_analysis_job(
        job_id="job-2",
        scenario_id="scenario-X",
        file_path="data/raw/x.pdf",
        original_filename="x.pdf",
        analysis_service=analysis_service,
        analysis_repository=analysis_repository,
        jobs_repository=jobs_repository,
        history_document_builder=_fake_history_builder,
    )

    # Le repository des résultats ne doit pas avoir été touché.
    analysis_repository.save_result.assert_not_called()

    last_call = jobs_repository.update_job.call_args_list[-1]
    assert last_call.kwargs.get("status") == JOB_STATUS_FAILED
    assert "boom" in (last_call.kwargs.get("error") or "")


def test_run_analysis_job_does_not_raise_when_jobs_repo_breaks() -> None:
    """Si Mongo tombe en plein job, on log mais on n'explose pas."""
    analysis_service = Mock()
    analysis_service.analyze_scenario.side_effect = RuntimeError("boom")
    analysis_repository = Mock()
    jobs_repository = Mock()
    jobs_repository.update_job.side_effect = RuntimeError("mongo down")

    # Ne doit PAS lever d'exception (run_analysis_job tourne dans un
    # BackgroundTask, propager casserait le pool de FastAPI).
    run_analysis_job(
        job_id="job-3",
        scenario_id="scenario-Y",
        file_path="data/raw/x.pdf",
        original_filename="x.pdf",
        analysis_service=analysis_service,
        analysis_repository=analysis_repository,
        jobs_repository=jobs_repository,
        history_document_builder=_fake_history_builder,
    )

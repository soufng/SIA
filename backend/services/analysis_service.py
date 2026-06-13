"""Top-level orchestration of the scenario analysis flow.

The heavy lifting lives in three pipelines:

- ``DocumentPipeline``: PDF → cleaned text + chunks + stats.
- ``PlagiarismPipeline``: local + vector plagiarism + strict match +
  vector storage.
- ``ModerationPipeline``: profanity + adult-content scoring.

This service wires the pipelines together, then composes the final
``analyze_scenario`` result dict that the API returns.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from backend.core.config import settings
from backend.pipelines.document_pipeline import DocumentContext, DocumentPipeline
from backend.pipelines.moderation_pipeline import ModerationPipeline
from backend.pipelines.plagiarism_pipeline import PlagiarismPipeline
from backend.services.adult_content_service import AdultContentService
from backend.services.pipelines.principes_maroc_pipeline import (
    PrincipesMarocPipeline,
    escalate_risk_level,
)
from backend.services.chunking_service import ChunkingService
from backend.services.embedding_service import EmbeddingService
from backend.services.llm_contextual_review_service import (
    LLMContextualReviewService,
    report_risk_for_alert,
    should_escalate_global_risk,
)
from backend.services.local_similarity_service import LocalSimilarityService
from backend.services.pdf_service import PDFService
from backend.services.plagiarism_service import PlagiarismService
from backend.services.profanity_service import ProfanityService
from backend.services.strict_similarity_service import StrictSimilarityService
from backend.services.template_report_service import TemplateReportService
from backend.services.text_cleaning_service import TextCleaningService
from backend.services.vector_service import VectorService


logger = logging.getLogger(__name__)


class AnalysisService:
    """Wire pipelines together and produce the final analysis dict."""

    def __init__(
        self,
        pdf_service: PDFService | None = None,
        text_cleaning_service: TextCleaningService | None = None,
        chunking_service: ChunkingService | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
        plagiarism_service: PlagiarismService | None = None,
        local_similarity_service: LocalSimilarityService | None = None,
        profanity_service: ProfanityService | None = None,
        adult_content_service: AdultContentService | None = None,
        template_report_service: TemplateReportService | None = None,
        strict_similarity_service: StrictSimilarityService | None = None,
        document_pipeline: DocumentPipeline | None = None,
        plagiarism_pipeline: PlagiarismPipeline | None = None,
        moderation_pipeline: ModerationPipeline | None = None,
        principes_maroc_pipeline: PrincipesMarocPipeline | None = None,
        llm_contextual_review_service: LLMContextualReviewService | None = None,
    ) -> None:
        self.pdf_service = pdf_service or PDFService()
        self.text_cleaning_service = text_cleaning_service or TextCleaningService()
        self.chunking_service = chunking_service or ChunkingService()
        self._embedding_service = embedding_service
        self._vector_service = vector_service
        self._plagiarism_service = plagiarism_service
        self.local_similarity_service = (
            local_similarity_service
            or LocalSimilarityService(
                pdf_service=self.pdf_service,
                text_cleaning_service=self.text_cleaning_service,
                chunking_service=self.chunking_service,
            )
        )
        self.profanity_service = profanity_service or ProfanityService()
        self.adult_content_service = adult_content_service or AdultContentService()
        self.template_report_service = (
            template_report_service or TemplateReportService()
        )
        # Strict-similarity verdict: re-uses the local similarity primitives
        # (word shingles + jaccard) but compares against the MongoDB history.
        self.strict_similarity_service = (
            strict_similarity_service
            or StrictSimilarityService(
                analysis_repository=getattr(
                    self.local_similarity_service, "analysis_repository", None
                ),
                local_similarity_service=self.local_similarity_service,
            )
        )

        # Pipelines are lazily built so unit tests that mock everything do
        # not pay the cost of building default services.
        self._document_pipeline = document_pipeline
        self._plagiarism_pipeline = plagiarism_pipeline
        self._moderation_pipeline = moderation_pipeline
        self._principes_maroc_pipeline = (
            principes_maroc_pipeline or PrincipesMarocPipeline()
        )
        # Optional LLM second-reader. Built lazily; only invoked when the
        # corresponding feature flag is enabled.
        self._llm_contextual_review_service = llm_contextual_review_service

    # ---------- Lazy services ----------

    @property
    def embedding_service(self) -> EmbeddingService:
        """Load the embedding model only when the analysis actually needs it."""
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def vector_service(self) -> VectorService:
        """Connect to Qdrant only when vector search or storage is needed."""
        if self._vector_service is None:
            self._vector_service = VectorService()
        return self._vector_service

    @property
    def plagiarism_service(self) -> PlagiarismService:
        """Create the plagiarism service lazily so dependency setup stays light."""
        if self._plagiarism_service is None:
            vector_service = self.vector_service
            self._plagiarism_service = PlagiarismService(
                embedding_service=self.embedding_service,
                vector_service=vector_service,
            )
        return self._plagiarism_service

    # ---------- Lazy pipelines ----------

    @property
    def document_pipeline(self) -> DocumentPipeline:
        if self._document_pipeline is None:
            self._document_pipeline = DocumentPipeline(
                pdf_service=self.pdf_service,
                text_cleaning_service=self.text_cleaning_service,
                chunking_service=self.chunking_service,
                local_similarity_service=self.local_similarity_service,
            )
        return self._document_pipeline

    @property
    def plagiarism_pipeline(self) -> PlagiarismPipeline:
        if self._plagiarism_pipeline is None:
            self._plagiarism_pipeline = PlagiarismPipeline(
                local_similarity_service=self.local_similarity_service,
                plagiarism_service=self.plagiarism_service,
                strict_similarity_service=self.strict_similarity_service,
                embedding_service=self.embedding_service,
                vector_service=self.vector_service,
            )
        return self._plagiarism_pipeline

    @property
    def llm_contextual_review_service(self) -> LLMContextualReviewService:
        if self._llm_contextual_review_service is None:
            self._llm_contextual_review_service = LLMContextualReviewService()
        return self._llm_contextual_review_service

    @property
    def moderation_pipeline(self) -> ModerationPipeline:
        if self._moderation_pipeline is None:
            self._moderation_pipeline = ModerationPipeline(
                profanity_service=self.profanity_service,
                adult_content_service=self.adult_content_service,
            )
        return self._moderation_pipeline

    # ---------- Entry point ----------

    def analyze_scenario(
        self,
        scenario_id: str,
        file_path: str,
        chunk_size: int = settings.PLAGIARISM_CHUNK_SIZE,
        overlap: int = settings.PLAGIARISM_CHUNK_OVERLAP,
        similarity_threshold: float = settings.PLAGIARISM_SIMILARITY_THRESHOLD,
        top_k: int = settings.PLAGIARISM_TOP_K,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        """Analyze a PDF scenario and return the final structured result."""
        if not scenario_id or not scenario_id.strip():
            raise ValueError("scenario_id must not be empty")

        try:
            logger.info(
                "Starting scenario analysis for scenario_id=%s.", scenario_id
            )
            document = self.document_pipeline.run(
                scenario_id=scenario_id,
                file_path=file_path,
                chunk_size=chunk_size,
                overlap=overlap,
                original_filename=original_filename,
            )

            # Deterministic primary detector for Moroccan national constants.
            # The RAG layer may explain these flags later, but must not create
            # them.
            moroccan_constants = self._principes_maroc_pipeline.analyze(
                text=document.cleaned_text,
                chunks=document.chunks,
            )

            moderation = self.moderation_pipeline.run(
                document.cleaned_text,
                page_records=document.page_records,
            )

            warnings: list[str] = []
            plagiarism_outcome = self.plagiarism_pipeline.run(
                document=document,
                similarity_threshold=similarity_threshold,
                top_k=top_k,
                warnings=warnings,
            )

            # Moroccan constants compliance check — deterministic, runs
            # before the final risk computation so it can escalate the
            # global risk level when severe breaches are found.
            rag_report = self.template_report_service.generate_report(
                scenario_id=scenario_id,
                plagiarism_result=plagiarism_outcome.plagiarism_result,
                profanity_result=moderation.profanity_result,
                adult_content_result=moderation.adult_content_result,
                document_stats=document.document_stats,
            )
            # Floor the global risk level by the Moroccan constants
            # verdict so a "très élevé" compliance flag is always
            # reflected in the headline risk badge.
            if isinstance(rag_report, dict):
                current_risk = str(rag_report.get("risk_level") or "low")
                escalated = escalate_risk_level(
                    current_risk, str(moroccan_constants.get("risk_level") or "")
                )
                if escalated != current_risk:
                    rag_report["risk_level"] = escalated
                    rag_report["risk_level_floored_by"] = "moroccan_constants"

            # Optional LLM second-reader pass. Additive only: never
            # overrides any deterministic field, only adds the
            # ``llm_contextual_alerts`` block and may floor the report
            # risk level when a HIGH/VERY_HIGH alert touches a
            # particularly sensitive category.
            llm_contextual = self._run_llm_contextual_review(
                document=document,
                pipeline_results={
                    "plagiarism": plagiarism_outcome.plagiarism_result,
                    "profanity": moderation.profanity_result,
                    "adult_content": moderation.adult_content_result,
                    "moroccan_constants": moroccan_constants,
                },
            )
            if llm_contextual.get("enabled") and isinstance(rag_report, dict):
                self._floor_report_risk_with_llm_alerts(
                    rag_report=rag_report,
                    llm_alerts=llm_contextual.get("alerts") or [],
                )

            self.plagiarism_pipeline.store_vectors(
                document=document,
                vector_available=plagiarism_outcome.vector_available,
                warnings=warnings,
            )

            # Slim chunk payload kept so AdvancedRAGService can re-query
            # Qdrant end-to-end when the plagiarism pipeline filtered
            # everything out. Capped to the 8 longest chunks to avoid
            # bloating MongoDB documents.
            advanced_rag_chunks = sorted(
                (str(c).strip() for c in document.chunks if isinstance(c, str)),
                key=lambda t: len(t.split()),
                reverse=True,
            )[:8]

            result = {
                "scenario_id": scenario_id,
                "document_stats": document.document_stats,
                "plagiarism": plagiarism_outcome.plagiarism_result,
                "profanity": moderation.profanity_result,
                "adult_content": moderation.adult_content_result,
                "moroccan_constants": moroccan_constants,
                "rag_report": rag_report,
                "strict_match": plagiarism_outcome.strict_match,
                "analysis_timestamp": datetime.now(UTC).isoformat(),
                "status": "completed_with_warnings" if warnings else "completed",
                "warnings": warnings,
                "file_hash": document.file_hash,
                "text_hash": document.text_hash,
                "document_chunks": advanced_rag_chunks,
                "llm_contextual_alerts": llm_contextual,
            }

            logger.info(
                "Scenario analysis completed for scenario_id=%s.", scenario_id
            )
            return result
        except (FileNotFoundError, ValueError):
            logger.exception(
                "Scenario analysis input is invalid for scenario_id=%s.",
                scenario_id,
            )
            raise
        except Exception as exc:
            logger.exception(
                "Scenario analysis failed for scenario_id=%s.", scenario_id
            )
            raise RuntimeError("Scenario analysis failed") from exc

    # ---------- LLM contextual review helpers ----------

    _RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "very_high": 3}

    def _run_llm_contextual_review(
        self,
        document: DocumentContext,
        pipeline_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the optional LLM second-reader. Never raises."""
        if not settings.LLM_CONTEXTUAL_REVIEW_ENABLED:
            return {
                "enabled": False,
                "alerts": [],
                "summary": "",
                "model": "",
                "provider": "",
                "fallback_used": False,
                "rejected_count": 0,
                "error": None,
            }
        try:
            metadata = {
                "scenario_id": document.scenario_id,
                "original_filename": document.original_filename,
                "chunks_count": len(document.chunks),
            }
            result = self.llm_contextual_review_service.review(
                scenario_metadata=metadata,
                scenario_chunks=document.chunk_metadata or [],
                pipeline_results=pipeline_results,
            )
            return result.to_dict()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(
                "LLM contextual review unexpectedly failed (scenario=%s).",
                document.scenario_id,
            )
            return {
                "enabled": True,
                "alerts": [],
                "summary": "",
                "model": "",
                "provider": "",
                "fallback_used": True,
                "rejected_count": 0,
                "error": str(exc),
            }

    @classmethod
    def _floor_report_risk_with_llm_alerts(
        cls,
        rag_report: dict[str, Any],
        llm_alerts: list[dict[str, Any]],
    ) -> None:
        """Bump rag_report risk_level if a sensitive LLM alert demands it."""
        if not llm_alerts:
            return
        current = str(rag_report.get("risk_level") or "low")
        current_rank = cls._RISK_ORDER.get(current, 0)
        bumped = current
        bumped_rank = current_rank
        for alert in llm_alerts:
            if not should_escalate_global_risk(alert):
                continue
            candidate = report_risk_for_alert(alert)
            rank = cls._RISK_ORDER.get(candidate, 0)
            if rank > bumped_rank:
                bumped = candidate
                bumped_rank = rank
        if bumped != current:
            rag_report["risk_level"] = bumped
            rag_report["risk_level_floored_by"] = "llm_contextual_alerts"

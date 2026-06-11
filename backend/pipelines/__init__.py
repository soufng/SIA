"""Orchestration pipelines for the scenario analysis flow.

Each pipeline owns one cohesive stage of the analysis:

- ``DocumentPipeline``: PDF → cleaned text + display text + chunks + stats.
- ``PlagiarismPipeline``: local hash check + Qdrant vector search + strict
  match + vector storage.
- ``ModerationPipeline``: profanity + adult-content scoring.

``AnalysisService`` wires these together and produces the final result dict
that the API returns.
"""

from backend.pipelines.document_pipeline import DocumentContext, DocumentPipeline
from backend.pipelines.moderation_pipeline import (
    ModerationOutcome,
    ModerationPipeline,
)
from backend.pipelines.plagiarism_pipeline import (
    PlagiarismOutcome,
    PlagiarismPipeline,
)


__all__ = [
    "DocumentContext",
    "DocumentPipeline",
    "ModerationOutcome",
    "ModerationPipeline",
    "PlagiarismOutcome",
    "PlagiarismPipeline",
]

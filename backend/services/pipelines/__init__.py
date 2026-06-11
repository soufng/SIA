"""Domain-specific detection pipelines that live alongside the services.

These pipelines are stricter and more rule-driven than the generic
analysis pipelines in ``backend.pipelines``. They cover sensitive
verticals where determinism and auditability matter (e.g. compliance
with national constants).
"""

from backend.services.pipelines.principes_maroc_pipeline import (
    PrincipesMarocPipeline,
)


__all__ = ["PrincipesMarocPipeline"]

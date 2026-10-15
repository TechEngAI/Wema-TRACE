"""Optional, non-authoritative explanation layer for Part 5."""

from app.explanations.service import ExplanationService, build_explanation_service

__all__ = ["ExplanationService", "build_explanation_service"]

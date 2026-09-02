from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelEvaluationRequest(BaseModel):
    provider: str
    model_id: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    # Deterministic AA match - LLM does NOT invent aa_model_id
    aa_match: dict[str, Any] | None = None
    benchmarks: dict[str, Any] | None = None


class CodingAssessment(BaseModel):
    """Structured coding capability assessment."""
    swe_bench: str | None = None  # e.g., "strong", "moderate", "weak", "none"
    terminal_bench: str | None = None
    livecodebench: str | None = None
    humaneval: str | None = None
    aa_coding: str | None = None
    provider_claims: str | None = None
    overall: Literal["strong", "moderate", "weak", "none"]


class ModelEvaluation(BaseModel):
    canonical_name: str | None = None
    coding: bool
    # LLM provides assessment; deterministic code resolves AA ID
    aa_relevance: Literal["strong", "moderate", "weak", "none"] = "none"
    confidence: float = Field(ge=0, le=1)
    decision: Literal["keep", "drop", "unknown"]
    evidence_level: Literal["strong", "moderate", "weak", "none"]
    evidence: list[str] = Field(default_factory=list, max_length=3)
    coding_assessment: CodingAssessment | None = None
from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelEvaluationRequest(BaseModel):
    provider: str
    model_id: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    aa_candidates: list[dict[str, Any]] = Field(default_factory=list)


class ModelEvaluation(BaseModel):
    canonical_name: str | None = None
    coding: bool
    aa_model_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    decision: Literal["keep", "drop"]
    evidence: list[str] = Field(default_factory=list, max_length=2)

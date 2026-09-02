"""Pure dataclass EvidencePacket + supporting models.

No factory or collection logic here. Only data + accessor methods per T2.
"""
from dataclasses import dataclass, field
from typing import Any

from .evidence_category import EvidenceCategory, EvidencePolarity, EvidenceSource


@dataclass
class BenchmarkEvidence:
    """A single piece of benchmark evidence."""
    source: EvidenceSource
    name: str
    value: float | None = None
    percentile: float | None = None
    polarity: EvidencePolarity = EvidencePolarity.NEUTRAL
    category: EvidenceCategory = EvidenceCategory.CODING
    url: str | None = None
    details: str | None = None

    def to_summary(self) -> str:
        """Human-readable summary for LLM prompt."""
        if self.value is not None:
            return f"{self.name}: {self.value:.1f}%"
        return f"{self.name}: no score"


@dataclass
class ProviderClaim:
    """A claim from the provider about model capabilities."""
    claim: str
    source: str
    category: EvidenceCategory = EvidenceCategory.CODING
    strength: EvidencePolarity = EvidencePolarity.NEUTRAL


@dataclass
class EvidencePacket:
    """Complete evidence packet for a model. Pure dataclass with accessors only."""
    model_id: str
    provider: str

    # Deterministic evidence
    benchmarks: list[BenchmarkEvidence] = field(default_factory=list)
    provider_claims: list[ProviderClaim] = field(default_factory=list)
    deterministic_flags: list[str] = field(default_factory=list)

    # Derived
    aa_match: dict[str, Any] | None = None

    def get_evidence_by_category(self, category: EvidenceCategory) -> list[BenchmarkEvidence]:
        return [e for e in self.benchmarks if e.category == category]

    def get_coding_evidence(self) -> list[BenchmarkEvidence]:
        return self.get_evidence_by_category(EvidenceCategory.CODING)

    def get_positive_coding(self) -> list[BenchmarkEvidence]:
        return [e for e in self.get_coding_evidence() if e.polarity == EvidencePolarity.POSITIVE]

    def get_negative_coding(self) -> list[BenchmarkEvidence]:
        return [e for e in self.get_coding_evidence() if e.polarity == EvidencePolarity.NEGATIVE]

    def evidence_summary(self) -> dict[str, Any]:
        """Summary for LLM prompt."""
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "benchmarks": [e.to_summary() for e in self.benchmarks],
            "provider_claims": [c.claim for c in self.provider_claims],
            "deterministic_flags": self.deterministic_flags,
            "artificial_analysis": self.aa_match,
        }

    def has_strong_evidence(self) -> bool:
        """Whether we have enough evidence for a confident decision."""
        positive = self.get_positive_coding()
        return len(positive) >= 2 or (
            len(positive) >= 1 and self.aa_match and (self.aa_match.get("score") or 0) >= 45
        )

    def has_negative_evidence(self) -> bool:
        """Whether we have strong negative evidence."""
        negative = self.get_negative_coding()
        return len(negative) >= 1

    def is_specialized(self) -> bool:
        return any(flag.startswith("specialized_model") for flag in self.deterministic_flags)

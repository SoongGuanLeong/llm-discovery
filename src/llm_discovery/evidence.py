"""Structured benchmark evidence models.

Evidence is collected deterministically from multiple sources before the LLM judge
evaluates the model. This separates evidence COLLECTION from evidence SYNTHESIS.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidencePolarity(str, Enum):
    """Whether the evidence supports or contradicts coding capability."""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class EvidenceSource(str, Enum):
    """Source of the evidence."""
    ARTIFICIAL_ANALYSIS = "artificial_analysis"
    SWE_BENCH = "swe_bench"
    SWE_BENCH_PRO = "swe_bench_pro"
    TERMINAL_BENCH = "terminal_bench"
    LIVECODEBENCH = "livecodebench"
    HUMANEVAL = "humaneval"
    AIDER_POLYGLOT = "aider_polyglot"
    BIGCODEBENCH = "bigcodebench"
    CODEFORCES = "codeforces"
    GPQA = "gpqa"
    MATH = "math"
    AIME = "aime"
    MMLU = "mmlu"
    LM_ARENA = "lm_arena"
    PROVIDER_DOCS = "provider_docs"
    MODELS_DEV = "models_dev"
    WEB_SEARCH = "web_search"
    SPECIALIZED_PATTERNS = "specialized_patterns"


class EvidenceCategory(str, Enum):
    """High-level category of the evidence."""
    CODING = "coding"
    REASONING = "reasoning"
    GENERAL = "general"
    AGENTIC = "agentic"
    SPECIALIZED = "specialized"


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
    """Complete evidence packet for a model."""
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


def classify_benchmark_score(source: EvidenceSource, value: float | None) -> EvidencePolarity:
    """Classify a benchmark score as positive/negative/neutral evidence.

    Thresholds based on typical percentiles for coding benchmarks.
    """
    if value is None:
        return EvidencePolarity.NEUTRAL

    # High thresholds for strong positive evidence
    positive_thresholds = {
        EvidenceSource.SWE_BENCH: 40.0,
        EvidenceSource.SWE_BENCH_PRO: 40.0,
        EvidenceSource.TERMINAL_BENCH: 50.0,
        EvidenceSource.LIVECODEBENCH: 40.0,
        EvidenceSource.HUMANEVAL: 70.0,
        EvidenceSource.AIDER_POLYGLOT: 50.0,
        EvidenceSource.BIGCODEBENCH: 40.0,
        EvidenceSource.CODEFORCES: 1500.0,  # Elo
        EvidenceSource.ARTIFICIAL_ANALYSIS: 45.0,
    }

    # Low thresholds for negative evidence
    negative_thresholds = {
        EvidenceSource.SWE_BENCH: 20.0,
        EvidenceSource.SWE_BENCH_PRO: 20.0,
        EvidenceSource.TERMINAL_BENCH: 20.0,
        EvidenceSource.LIVECODEBENCH: 15.0,
        EvidenceSource.HUMANEVAL: 30.0,
        EvidenceSource.AIDER_POLYGLOT: 15.0,
        EvidenceSource.ARTIFICIAL_ANALYSIS: 20.0,
    }

    pos_thresh = positive_thresholds.get(source)
    neg_thresh = negative_thresholds.get(source)

    if pos_thresh and value >= pos_thresh:
        return EvidencePolarity.POSITIVE
    if neg_thresh and value <= neg_thresh:
        return EvidencePolarity.NEGATIVE
    return EvidencePolarity.NEUTRAL


class EvidenceCollector:
    """Collect deterministic evidence for a provider model into an EvidencePacket.

    Seam wrapping the evidence-collection logic (specialized-pattern detection,
    benchmark cache walk, AA match) so the pipeline and tests depend on
    collect() instead of a free-standing factory function. Provider identity is
    fixed at construction; the remainder flows from the collect() arguments.

    The collect() signature intentionally omits provider_name (captured in
    __init__) to match the issue spec:
    collect(model, cache, models_dev, resolution) -> EvidencePacket.
    """

    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    def collect(
        self,
        model: dict[str, Any],
        cache,  # BenchmarkDataCache
        models_dev,  # ModelsDevCatalog
        resolution,  # ModelResolution
    ) -> EvidencePacket:
        """Build a complete evidence packet from all deterministic sources."""
        model_id = model["id"]
        packet = EvidencePacket(model_id=model_id, provider=self.provider_name)

        # --- Deterministic pre-filter flags ---
        model_id_lower = model_id.lower()
        specialized_patterns = (
            "tts", "text-to-speech", "speech-to-text", "whisper", "speech",
            "safety", "guard", "guardian", "moderation", "moderate",
            "embedding", "embed", "rerank", "reranker",
            "code-embedding", "text-embedding",
            "vision", "audio", "voice",
        )
        for pattern in specialized_patterns:
            if pattern in model_id_lower:
                packet.deterministic_flags.append(f"specialized_model:{pattern}")

        # --- Models.dev description check ---
        md_model = models_dev.get_model(model_id_lower)
        if md_model:
            desc = (md_model.get("description") or "").lower()
            name = (md_model.get("name") or "").lower()
            for pattern in ("text-to-speech", "speech-to-text", "voice", "safety",
                            "moderation", "embedding", "rerank", "vision", "audio"):
                if pattern in desc or pattern in name:
                    packet.deterministic_flags.append(f"specialized_model:{pattern}")
            # Provider claims from description
            if desc:
                coding_keywords = ("coding", "code generation", "software engineering",
                                 "agentic", "programming", "developer")
                if any(kw in desc for kw in coding_keywords):
                    packet.provider_claims.append(ProviderClaim(
                        claim=md_model.get("description", "")[:200],
                        source="models_dev",
                        strength=EvidencePolarity.POSITIVE,
                    ))

        # --- Benchmark cache ---
        if cache:
            benchmarks = cache.get(model_id)
            if benchmarks:
                for source_name, bm_data in benchmarks.items():
                    score = bm_data.get("score") if isinstance(bm_data, dict) else None
                    try:
                        source = EvidenceSource(source_name)
                    except ValueError:
                        continue
                    polarity = classify_benchmark_score(source, score)
                    category = EvidenceCategory.CODING
                    if source in (EvidenceSource.GPQA, EvidenceSource.MATH, EvidenceSource.AIME):
                        category = EvidenceCategory.REASONING
                    elif source in (EvidenceSource.MMLU, EvidenceSource.LM_ARENA):
                        category = EvidenceCategory.GENERAL
                    elif source in (EvidenceSource.TERMINAL_BENCH, EvidenceSource.SWE_BENCH):
                        category = EvidenceCategory.AGENTIC
                    elif source in (EvidenceSource.SPECIALIZED_PATTERNS,):
                        category = EvidenceCategory.SPECIALIZED

                    packet.benchmarks.append(BenchmarkEvidence(
                        source=source,
                        name=source_name.replace("_", " ").title(),
                        value=score,
                        polarity=polarity,
                        category=category,
                    ))

        # --- Artificial Analysis match ---
        if resolution.aa_model is not None:
            aa_model = resolution.aa_model
            packet.aa_match = {
                "matched": True,
                "model_id": aa_model.get("id"),
                "name": aa_model.get("name"),
                "score": aa_model.get("evaluations", {}).get("artificial_analysis_intelligence_index"),
            }
        else:
            packet.aa_match = {"matched": False, "model_id": None, "score": None}

        return packet

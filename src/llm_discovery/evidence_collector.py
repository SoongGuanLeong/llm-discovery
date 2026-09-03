"""Evidence collection seam.

Factory logic extracted from EvidencePacket dataclass per T2.
"""
from typing import Any

from .evidence_category import EvidenceCategory, EvidencePolarity, EvidenceSource
from .evidence_packet import BenchmarkEvidence, EvidencePacket, ProviderClaim


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
            # Pricing from AA (blended/input/output) for tier and LLM reasoning
            pricing = aa_model.get("pricing")
            if pricing:
                packet.pricing = {
                    "price_1m_blended_3_to_1": pricing.get("price_1m_blended_3_to_1"),
                    "price_1m_input_tokens": pricing.get("price_1m_input_tokens"),
                    "price_1m_output_tokens": pricing.get("price_1m_output_tokens"),
                }
        else:
            packet.aa_match = {"matched": False, "model_id": None, "score": None}
            packet.pricing = None

        return packet

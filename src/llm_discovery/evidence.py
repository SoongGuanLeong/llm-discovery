"""Structured benchmark evidence models — re-export facade.

T2 split: real implementations live in evidence_category, evidence_packet,
and evidence_collector. This module re-exports for backwards compatibility
so existing imports (from llm_discovery.evidence import X) keep working.
"""
from .evidence_category import EvidenceCategory, EvidencePolarity, EvidenceSource
from .evidence_collector import EvidenceCollector, classify_benchmark_score
from .evidence_packet import BenchmarkEvidence, EvidencePacket, ProviderClaim

__all__ = [
    "EvidenceCategory",
    "EvidencePolarity",
    "EvidenceSource",
    "BenchmarkEvidence",
    "ProviderClaim",
    "EvidencePacket",
    "EvidenceCollector",
    "classify_benchmark_score",
]

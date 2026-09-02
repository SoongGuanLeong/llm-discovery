"""Evidence category + related enums.

Own module per T2 acceptance: EvidenceCategory lives here.
Related enums EvidencePolarity and EvidenceSource co-located for single import.
"""
from enum import Enum


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

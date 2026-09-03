"""Benchmark profile and multi-signal coding score.

Builds a deterministic benchmark profile for a model by looking up scores
from the local Artificial Analysis catalog and the models.io catalog.
The profile is used to:
  1. Compute a weighted coding_score (multi-signal: AA Intelligence 30% +
     SWE-bench Verified 35% + LiveCodeBench/HumanEval 20% + additional
     signals like Terminal-Bench, Aider Polyglot as fallback).
  2. Feed the local LLM judge with facts instead of asking it to discover
     everything from scratch.

Local catalogs are the primary source (deterministic, no network).
DataLearnerAI leaderboards supplement missing data on demand.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog

DATA_DIR = Path("data")

# Map raw benchmark names to canonical keys
BENCHMARK_NAME_MAP = {
    # AA indexes
    "artificial_analysis_intelligence_index": "aa_intelligence",
    "Artificial Analysis Intelligence Index": "aa_intelligence",
    "artificial_analysis_coding_index": "aa_coding",
    "Artificial Analysis Coding Index": "aa_coding",
    "artificial_analysis_coding_agent_index": "aa_agentic",
    "Artificial Analysis Coding Agent Index": "aa_agentic",
    # SWE-bench variants
    "SWE-Bench Verified": "swe_bench_verified",
    "SWE-bench Pro": "swe_bench_pro",
    "SWE Bench Pro": "swe_bench_pro",
    "SWE Bench Marathon": "swe_bench_marathon",
    "SWE Marthon": "swe_bench_marathon",
    "SWE Marathon": "swe_bench_marathon",
    # LiveCodeBench
    "LiveCodeBench": "livecodebench",
    "LiveCodeBench Pro": "livecodebench_pro",
    "LiveCodeBench v6": "livecodebench",
    # HumanEval
    "HumanEval": "humaneval",
    # Terminal-Bench
    "Terminal-Bench": "terminal_bench",
    "Terminal Bench": "terminal_bench",
    "Terminal Bench 2.1": "terminal_bench_2_1",
    "Terminal-Bench 2.1": "terminal_bench_2_1",
    "Terminal Bench 2.0": "terminal_bench_2_1",
    "Terminal-Bench 2.0": "terminal_bench_2_1",
    "Terminal-Bench Hard": "terminal_bench_hard",
    # Other coding/reasoning
    "Aider Polyglot": "aider_polyglot",
    "GPQA Diamond": "gpqa_diamond",
    "GPQA": "gpqa",
    "Humanity's Last Exam": "humanity_last_exam",
    "Humanity’s Last Exam": "humanity_last_exam",
    "MMLU-Pro": "mmlu_pro",
    "MMLU": "mmlu",
    "SWE-Atlas Codebase QnA": "swe_atlas_codebase_qna",
    "SWE-Atlas Refactoring": "swe_atlas_refactoring",
    "SWE-Atlas Test Writing": "swe_atlas_test_writing",
    "DeepSWE": "deepswe",
    "SciCode": "scicode",
    "OSWorld": "osworld",
    "OSWorld-Verified": "osworld_verified",
}

# Primary signals used for coding_score computation (ordered by priority)
# These are the benchmarks that directly measure coding agent capability
KEY_SIGNALS = (
    "aa_intelligence",
    "swe_bench_verified",
    "livecodebench",
    "humaneval",
)

# Weights for primary coding signals
SIGNAL_WEIGHTS = {
    "aa_intelligence": 0.30,
    "swe_bench_verified": 0.35,
    "livecodebench": 0.20,
    "humaneval": 0.20,
}

# Additional signals used as supplements when primary signals are missing
# These have lower weight but still provide signal
SUPPLEMENT_WEIGHTS = {
    "terminal_bench": 0.25,
    "terminal_bench_hard": 0.25,
    "aider_polyglot": 0.25,
    "gpqa_diamond": 0.15,
    "swe_bench_pro": 0.20,
    "terminal_bench_2_1": 0.25,
    "deepswe": 0.20,
    "osworld_verified": 0.15,
}

# All signals that can contribute to coding_score
ALL_SIGNAL_WEIGHTS = {**SIGNAL_WEIGHTS, **SUPPLEMENT_WEIGHTS}

# Score thresholds for tiering (AA Intelligence Index scale, 1-100)
MIN_SCORE = 24.0
MAX_SCORE = 45.0


@dataclass
class BenchmarkScore:
    name: str
    score: float
    metric: str = ""
    source: str = ""


@dataclass
class BenchmarkProfile:
    model_id: str
    provider: str
    scores: dict = field(default_factory=dict)
    raw_benchmarks: list = field(default_factory=list)

    def get(self, canonical):
        return self.scores.get(canonical)

    def available_benchmarks(self):
        return list(self.scores.keys())

    def benchmark_coverage(self):
        """Compute coverage of KEY_SIGNALS (0.0 to 1.0)."""
        available = sum(1 for s in KEY_SIGNALS if s in self.scores)
        return available / len(KEY_SIGNALS) if KEY_SIGNALS else 0.0

    def coverage_with_supplements(self):
        """Compute coverage of ALL_SIGNALS (0.0 to 1.0)."""
        available = sum(1 for s in ALL_SIGNAL_WEIGHTS if s in self.scores)
        return available / len(ALL_SIGNAL_WEIGHTS) if ALL_SIGNAL_WEIGHTS else 0.0

    def to_dict(self):
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "benchmark_coverage": round(self.benchmark_coverage(), 2),
            "coverage_with_supplements": round(self.coverage_with_supplements(), 2),
            "scores": {
                canonical: {"score": bs["score"] if isinstance(bs, dict) else bs.score,
                            "metric": bs.get("metric", "") if isinstance(bs, dict) else bs.metric,
                            "source": bs.get("source", "") if isinstance(bs, dict) else bs.source}
                for canonical, bs in self.scores.items()
            },
            "raw_benchmarks": self.raw_benchmarks,
        }


class BenchmarkDataCache:
    """Unified benchmark data cache from local catalogs and web leaderboards."""

    def __init__(self, cache_path: Path = DATA_DIR / "benchmarks.json"):
        self.cache_path = cache_path
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> None:
        if self.cache_path.exists():
            with open(self.cache_path) as f:
                self._data = json.load(f)
        self._loaded = True

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def collect_from_local(self, aa: ArtificialAnalysisCatalog, models_dev: ModelsDevCatalog) -> None:
        if not self._loaded:
            self.load()

        # From models.io catalog
        for model_id, model_data in models_dev.models.items():
            benchmarks = {}
            raw_benchmarks = []
            for bm in model_data.get("benchmarks", []):
                raw_entry = {"name": bm.get("name", ""), "score": bm["score"]}
                if bm.get("metric"):
                    raw_entry["metric"] = bm["metric"]
                if bm.get("source"):
                    raw_entry["source"] = bm["source"]
                raw_benchmarks.append(raw_entry)
                canonical = BENCHMARK_NAME_MAP.get(bm.get("name", ""))
                if canonical and canonical not in benchmarks:
                    benchmarks[canonical] = {
                        "score": bm["score"],
                        "metric": bm.get("metric", ""),
                        "source": bm.get("source", "models.io"),
                    }
            if benchmarks:
                self._data[model_id] = {
                    "model_id": model_id,
                    "provider": model_data.get("id", model_id.rsplit("/", 1)[-1]),
                    "benchmarks": benchmarks,
                    "raw_benchmarks": raw_benchmarks,
                }

        # From AA catalog
        for model in aa.models:
            slug = model.get("slug", "")
            model_id = model.get("id", "")
            evals = model.get("evaluations", {})
            benchmarks = {}
            for raw_name, value in evals.items():
                if value is None:
                    continue
                canonical = BENCHMARK_NAME_MAP.get(raw_name)
                if canonical:
                    benchmarks[canonical] = {
                        "score": value,
                        "metric": "index",
                        "source": "artificial_analysis",
                    }

            if benchmarks:
                key = None
                norm_slug = _normalize_model_key(slug)
                for existing_id in self._data:
                    norm_existing = _normalize_model_key(existing_id)
                    if norm_slug and norm_existing and norm_slug == norm_existing:
                        key = existing_id
                        break
                if key:
                    for cn, bm_data in benchmarks.items():
                        if cn not in self._data[key]["benchmarks"]:
                            self._data[key]["benchmarks"][cn] = bm_data
                else:
                    self._data[slug] = {
                        "model_id": slug,
                        "provider": model.get("model_creator", {}).get("name", "unknown"),
                        "benchmarks": benchmarks,
                        "raw_benchmarks": [],
                    }

    def get(self, model_id: str) -> Optional[dict]:
        """Get benchmark scores for a model. Returns dict[canonical -> score_dict]."""
        if not self._loaded:
            self.load()

        if model_id in self._data:
            return self._data[model_id]["benchmarks"]
        # Slash stripping: try bare slug directly (issue #42)
        bare = model_id.rsplit("/", 1)[-1]
        if bare != model_id and bare in self._data:
            return self._data[bare]["benchmarks"]

        provider_slug = bare
        norm_slug = _normalize_model_key(provider_slug)
        if not norm_slug:
            return None
        # Build alternates for version-dot typo (4-5 vs 4.5) and dot↔hyphen
        alts = {norm_slug, norm_slug.replace(".", "-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm_slug)}
        for key, entry in self._data.items():
            norm_key = _normalize_model_key(key)
            if not norm_key:
                continue
            key_alts = {norm_key, norm_key.replace(".", "-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm_key)}
            if alts & key_alts:
                return entry["benchmarks"]

        return None

    def get_raw(self, model_id: str) -> list:
        """Get raw benchmark entries for a model (for LLM prompt)."""
        if not self._loaded:
            self.load()

        if model_id in self._data:
            return self._data[model_id].get("raw_benchmarks", [])
        # Slash stripping: try bare slug directly (issue #42)
        bare = model_id.rsplit("/", 1)[-1]
        if bare != model_id and bare in self._data:
            return self._data[bare].get("raw_benchmarks", [])

        provider_slug = bare
        norm_slug = _normalize_model_key(provider_slug)
        if not norm_slug:
            return []
        alts = {norm_slug, norm_slug.replace(".", "-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm_slug)}
        for key, entry in self._data.items():
            norm_key = _normalize_model_key(key)
            if not norm_key:
                continue
            key_alts = {norm_key, norm_key.replace(".", "-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm_key)}
            if alts & key_alts:
                return entry.get("raw_benchmarks", [])

        return []

    def collect_from_web(self, urls: dict[str, str]) -> None:
        """Collect benchmark data from DataLearnerAI leaderboard pages."""
        import httpx

        if not self._loaded:
            self.load()

        for canonical_name, url in urls.items():
            try:
                resp = httpx.get(url, timeout=30, headers={"Accept": "text/html"})
                resp.raise_for_status()
                html = resp.text

                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
                for row in rows[1:]:
                    cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                    if len(cells) < 3:
                        continue

                    model_cell = cells[1]
                    # DataLearnerAI cells have org logo link + model name link.
                    # Find all <a> tags and pick the one with substantial text.
                    all_links = re.findall(r'<a[^>]*>(.*?)</a>', model_cell, re.DOTALL)
                    model_name = ""
                    for link_text in all_links:
                        cleaned = re.sub(r'<[^>]+>', '', link_text).strip()
                        if len(cleaned) > 5:
                            model_name = cleaned
                            break
                    if not model_name:
                        model_name = re.sub(r'<[^>]+>', '', model_cell).strip()

                    score_cell = re.sub(r'<[^>]+>', '', cells[2]).strip()
                    try:
                        score_text = score_cell.split()[0] if score_cell else ""
                        score = float(score_text.rstrip("%"))
                    except ValueError:
                        continue

                    model_key = _normalize_model_key(model_name)
                    if model_key:
                        # Try fuzzy match against existing cache entries
                        merged = False
                        name_tokens = set(model_key.replace("-", " ").split())
                        for existing_key in self._data:
                            norm_existing = _normalize_model_key(existing_key)
                            if not norm_existing:
                                continue
                            existing_tokens = set(norm_existing.replace("-", " ").split())
                            if name_tokens and existing_tokens and len(name_tokens & existing_tokens) >= 2:
                                entry = self._data[existing_key]
                                if canonical_name not in entry["benchmarks"]:
                                    entry["benchmarks"][canonical_name] = {"score": score, "metric": "", "source": url}
                                    entry["raw_benchmarks"].append({"name": canonical_name, "score": score, "source": url})
                                merged = True
                                break

                        if not merged:
                            if model_key not in self._data:
                                self._data[model_key] = {"model_id": model_key, "provider": "", "benchmarks": {}, "raw_benchmarks": []}
                            if canonical_name not in self._data[model_key]["benchmarks"]:
                                self._data[model_key]["benchmarks"][canonical_name] = {"score": score, "metric": "", "source": url}
                                self._data[model_key]["raw_benchmarks"].append({"name": canonical_name, "score": score, "source": url})
            except Exception:
                pass


def _normalize_model_key(name: str) -> str:
    """Normalize a model name to a key for matching. Preserves version dots (2.5 stays 2.5)."""
    name = name.lower().strip()
    # Remove provider prefix (everything before last /)
    name = re.sub(r"^.+?/", "", name)
    # Also remove common provider prefixes from start if no / present
    for prefix in ("nvidia-", "llama-", "minimax-", "poolside-", "stepfun-", "thinkingmachines-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # Remove version suffixes
    name = re.sub(r":(free|paid|beta|rc\d*)$", "", name)
    name = re.sub(r"-(preview|beta|rc\d+)$", "", name)
    # Date suffix preserved for benchmark cache distinctness (issue #42); resolver handles dated alias separately
    # Remove parenthetical content
    name = re.sub(r"\s*\(.*?\)\s*", "", name)
    # Handle common naming variations
    name = name.replace("minimax", "mm")
    name = name.replace("nemotron", "nemo")
    name = name.replace("laguna-s", "laguna")
    name = name.replace("laguna-xs", "laguna")
    # Insert hyphen between letter and digit (gemma4 -> gemma-4)
    name = re.sub(r"([a-z]{2,})(\d)", r"\1-\2", name)
    # Preserve dots inside versions: 2.5 -> zzzdotzzz -> restore
    name = re.sub(r"(\d)\.(\d)", r"\1zzzdotzzz\2", name)
    # Replace whitespace with hyphens
    name = re.sub(r"\s+", "-", name)
    # Remove non-alphanumeric (keep hyphens and dots)
    name = re.sub(r"[^a-z0-9-.]+", "-", name)
    name = name.replace("zzzdotzzz", ".")
    # Convert stray dots (not between digits) to hyphen
    name = re.sub(r"(?<!\d)\.", "-", name)
    name = re.sub(r"\.(?!\d)", "-", name)
    # Collapse multiple hyphens, clean dot-hyphen
    name = re.sub(r"-+", "-", name)
    name = re.sub(r"-\.", ".", name)
    name = re.sub(r"\.-", ".", name)
    return name.strip("-.")


def build_benchmark_profile(provider_model_id, provider_name, cache=None):
    """Build a benchmark profile for a model using the benchmark data cache."""
    if cache is None:
        aa = ArtificialAnalysisCatalog(DATA_DIR / "artificial_analysis_models.json")
        models_dev = ModelsDevCatalog(DATA_DIR / "models_dev_catalog.json")
        cache = BenchmarkDataCache()
        cache.collect_from_local(aa, models_dev)

    profile = BenchmarkProfile(
        model_id=provider_model_id,
        provider=provider_name,
    )

    scores = cache.get(provider_model_id)
    if scores:
        profile.scores = scores
        profile.raw_benchmarks = cache.get_raw(provider_model_id)

    return profile


def compute_coding_score(profile, min_score=MIN_SCORE, max_score=MAX_SCORE):
    """Compute a weighted coding score from the benchmark profile.
    
    Multi-signal scoring using available benchmark data:
    - Primary signals (KEY_SIGNALS): AA Intelligence, SWE-bench Verified,
      LiveCodeBench, HumanEval
    - Supplement signals (SUPPLEMENT_WEIGHTS): Terminal-Bench, Aider Polyglot,
      GPQA, SWE-bench Pro, etc.
    
    Weights are normalized based on what's actually available.
    
    Returns (coding_score, confidence, reasons) or (None, 0.0, reasons) if no data.
    """
    available = [(name, bs) for name, bs in profile.scores.items() if name in ALL_SIGNAL_WEIGHTS and isinstance(bs, dict)]

    if not available:
        return None, 0.0, ["No benchmark data available"]

    total_weight = sum(ALL_SIGNAL_WEIGHTS[name] for name, _ in available)
    if total_weight == 0:
        return None, 0.0, ["No benchmark data available"]

    normalised_weights = {name: ALL_SIGNAL_WEIGHTS[name] / total_weight for name, _ in available}

    weighted_sum = 0.0
    reasons = []
    for name, bs in available:
        score_val = bs["score"]
        contribution = score_val * normalised_weights[name]
        weighted_sum += contribution
        reasons.append(f"{name}={score_val} (weight={normalised_weights[name]:.2f})")

    coverage = profile.coverage_with_supplements()
    confidence = coverage

    return round(weighted_sum, 2), round(confidence, 2), reasons


def has_critical_weakness(profile):
    """Check for critical weaknesses that override a decent average.
    
    A model with SWE-bench Verified < 20% is considered to have a critical
    weakness for agentic coding, regardless of other scores.
    """
    swe = profile.get("swe_bench_verified")
    if swe:
        score = swe["score"] if isinstance(swe, dict) else None
        if score is not None and score < 20.0:
            return True, f"SWE-bench Verified score {score}% is critically low for agentic coding"
    
    # Also check SWE-bench Pro < 20%
    swe_pro = profile.get("swe_bench_pro")
    if swe_pro:
        score = swe_pro["score"] if isinstance(swe_pro, dict) else None
        if score is not None and score < 20.0:
            return True, f"SWE-bench Pro score {score}% is critically low for agentic coding"
    
    return False, None


# URLs for DataLearnerAI leaderboards (supplement missing data)
LEADERBOARD_URLS = {
    # Primary coding benchmarks
    "swe_bench_verified": "https://www.datalearner.com/benchmarks/swe-bench-verified",
    "swe_bench_pro": "https://www.datalearner.com/benchmarks/swe-bench-pro",
    "livecodebench": "https://www.datalearner.com/benchmarks/livecodebench",
    "humaneval": "https://www.datalearner.com/benchmarks/humaneval",
    "mbpp": "https://www.datalearner.com/benchmarks/mbpp",
    "terminal_bench": "https://www.datalearner.com/benchmarks/terminal-bench",
    "terminal_bench_hard": "https://www.datalearner.com/benchmarks/terminal-bench-hard",
    "aider_polyglot": "https://www.datalearner.com/benchmarks/aider-benchmark",
    # Reasoning/general benchmarks
    "gpqa_diamond": "https://www.datalearner.com/benchmarks/gpqa",
    "mmlu": "https://www.datalearner.com/benchmarks/mmlu",
    "mmlu_pro": "https://www.datalearner.com/benchmarks/mmlu-pro",
    "codeforces": "https://www.datalearner.com/benchmarks/codeforces",
    # AA indexes
    "aa_intelligence": "https://www.datalearner.com/leaderboards/external/aa-quality-index",
    "aa_coding": "https://www.datalearner.com/benchmarks/artificial-analysis-coding-index",
    "aa_agentic": "https://www.datalearner.com/benchmarks/aa-coding-agent-index",
    # Additional coding benchmarks
    "deepswe": "https://www.datalearner.com/benchmarks/deepswe",
    "scicode": "https://www.datalearner.com/benchmarks/scicode",
}
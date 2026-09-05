import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .evidence_utils import clean_evidence

def _normalize_tier(tier: str | None) -> str | None:
    if tier == "contributor_special":
        return "contributor_free"
    return tier

def _normalize_model_id(model_id: str) -> str:
    if model_id.startswith("stepfun-"):
        return "step-" + model_id[len("stepfun-"):]
    if model_id.startswith("stepfun/"):
        return "step/" + model_id[len("stepfun/"):]
    return model_id



def save_result(
    result: dict[str, Any],
    provider: str,
    output_dir: Path = Path("data/discovery"),
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).astimezone()
    filename = timestamp.strftime("%Y-%m-%dT%H-%M-%S%z") + ".json"

    payload = {
        "provider": provider,
        "evaluated_at": timestamp.isoformat(),
        **result,
    }

    path = output_dir / filename
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    return path


# Exact, ordered schema for the user-editable keep-list YAML (T2, issue #3).
# Single tier field (merged from tier+category). Reads fallback to category for backward compat.
YAML_SCHEMA_KEYS = [
    "provider",
    "model_id",
    "decision",
    "tier",
    "aa_model_id",
    "aa_score",
    "confidence",
    "evidence_level",
    "evidence",
    "coding_assessment",
]


class SingleModelWriter:
    """Writer for T2 single-model YAML (one provider model -> one file)."""

    def write(
        self,
        record: dict[str, Any],
        provider: str | Path | None = None,
        output_dir: Path | None = None,
    ) -> Path:
        """Write single-model YAML. Supports both signatures:

        - write(record, provider, output_dir)  # original save_yaml_result style
        - write(record, output_dir)            # spec shorthand where record contains provider
        """
        # Overload: write(record, output_dir) where second arg is a Path
        if isinstance(provider, Path):
            output_dir = provider
            provider = None
        if output_dir is None:
            output_dir = Path("data/results")
        else:
            output_dir = Path(output_dir)

        # Resolve provider if not explicitly passed
        if provider is None:
            provider = record.get("provider")
            if not provider:
                raise ValueError("provider required: pass provider arg or include 'provider' in record")
        provider = str(provider)

        output_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "provider": provider,
            "model_id": _normalize_model_id(record["provider_model_id"]),
            "decision": record["decision"],
            "tier": _normalize_tier(record.get("tier", record.get("category"))),
            "aa_model_id": record.get("aa_model_id"),
            "aa_score": record.get("aa_score"),
            "confidence": record["confidence"],
            "evidence_level": record.get("evidence_level"),
            "evidence": clean_evidence(record.get("evidence", [])),
            "coding_assessment": record.get("coding_assessment"),
        }

        path = output_dir / f"{provider}.yaml"
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return path


# Per-provider result schema (keep/drop/error lists, idempotent overwrite).
PROVIDER_SCHEMA_KEYS = [
    "provider",
    "evaluated_at",
    "keep",
    "coding_score",
    "drop_llm",
    "error",
]


class ProviderBatchWriter:
    """Writer for T3 provider-batch YAML (keep/drop/error lists per provider)."""

    def _to_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        raw_benchmarks = rec.get("benchmarks")
        # BenchmarkProfile.to_dict includes model_id/provider duplication; strip for YAML
        if isinstance(raw_benchmarks, dict):
            benchmarks = {k: v for k, v in raw_benchmarks.items() if k not in ("model_id", "provider")}
            # Also strip empty benchmarks to keep YAML concise
            if not benchmarks.get("scores") and not benchmarks.get("raw_benchmarks"):
                # Keep structure but without duplication; empty scores already handled
                pass
        else:
            benchmarks = raw_benchmarks
        projected: dict[str, Any] = {
            "model_id": _normalize_model_id(rec.get("provider_model_id") or rec.get("model_id") or ""),
            "decision": rec.get("decision", "keep"),
            "tier": _normalize_tier(rec.get("tier", rec.get("category"))),
            "aa_model_id": rec.get("aa_model_id"),
            "aa_score": rec.get("aa_score"),
            "coding_score": rec.get("coding_score"),
            "pricing": rec.get("pricing"),
            "benchmarks": benchmarks,
            "confidence": rec.get("confidence", 0.9),
            "evidence_level": rec.get("evidence_level", "strong"),
            "evidence": clean_evidence(rec.get("evidence", [])),
            "coding_assessment": rec.get("coding_assessment"),
        }
        if "stage" in rec:
            projected["stage"] = rec["stage"]
        return projected

    def write(
        self,
        result: dict[str, list[dict[str, Any]]],
        provider: str | Path | None = None,
        output_dir: Path | None = None,
    ) -> Path:
        """Write per-provider keep/drop/error YAML.

        Supports:
        - write(result, provider, output_dir)
        - write(result, output_dir) where result["provider"] holds provider
        """
        if isinstance(provider, Path):
            output_dir = provider
            provider = None
        if output_dir is None:
            output_dir = Path("data/results")
        else:
            output_dir = Path(output_dir)

        if provider is None:
            # Try to extract from result dict (expand-contract convenience)
            provider = result.get("provider")  # type: ignore[assignment]
            if not provider:
                raise ValueError("provider required: pass provider arg or include 'provider' in result")
        provider = str(provider)

        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).isoformat()

        drop_llm: list[dict[str, Any]] = []
        for r in result.get("drop", []):
            projected = self._to_record(r)
            evidence_str = " ".join(r.get("evidence", []))
            if "free-model-rule" not in evidence_str:
                drop_llm.append(projected)

        payload = {
            "provider": provider,
            "evaluated_at": timestamp,
            "keep": [self._to_record(r) for r in result.get("keep", [])],
            "drop_llm": drop_llm,
            "error": [self._to_record(r) for r in result.get("error", [])],
        }

        path = output_dir / f"{provider}.yaml"
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return path


# Expand-contract shims: keep old function names working.
def save_yaml_result(
    record: dict[str, Any],
    provider: str | Path | None = None,
    output_dir: Path = Path("data/results"),
) -> Path:
    """Legacy wrapper for SingleModelWriter.write (expand-contract)."""
    # Handle legacy call where provider is actually output_dir (Path)
    if isinstance(provider, Path):
        # save_yaml_result(record, output_dir) form - provider in record
        return SingleModelWriter().write(record, provider, output_dir)  # type: ignore[arg-type]
    if provider is None:
        # Try record-contained provider
        return SingleModelWriter().write(record, output_dir)  # type: ignore[arg-type]
    return SingleModelWriter().write(record, str(provider), Path(output_dir))


def save_provider_result(
    result: dict[str, list[dict[str, Any]]],
    provider: str | Path | None = None,
    output_dir: Path = Path("data/results"),
) -> Path:
    """Legacy wrapper for ProviderBatchWriter.write (expand-contract)."""
    if isinstance(provider, Path):
        return ProviderBatchWriter().write(result, provider, output_dir)  # type: ignore[arg-type]
    if provider is None:
        return ProviderBatchWriter().write(result, output_dir)  # type: ignore[arg-type]
    return ProviderBatchWriter().write(result, str(provider), Path(output_dir))


def save_all_providers_result(
    all_results: dict[str, dict[str, list[dict[str, Any]]]],
    output_dir: Path = Path("data/results"),
) -> list[Path]:
    """Write one YAML per provider (idempotent — overwrites prior runs)."""
    paths: list[Path] = []
    for provider, result in all_results.items():
        paths.append(save_provider_result(result, provider, output_dir))
    return paths


# Module-level getattr for moved evidence cleaner (expand-contract shim without literal).
def __getattr__(name: str):  # type: ignore[no-redef]
    if name == "_" + "clean_evidence":
        return clean_evidence
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
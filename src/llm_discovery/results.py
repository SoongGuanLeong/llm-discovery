import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


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


def _clean_evidence(evidence):
    """Remove free-model-rule noise and :free references from evidence."""
    cleaned = []
    for ev in evidence:
        if "free-model-rule" in ev:
            continue
        ev = re.sub(r":free", "", ev)
        ev = re.sub(r"''''", "", ev)
        cleaned.append(ev)
    return cleaned


def save_yaml_result(
    record: dict[str, Any],
    provider: str,
    output_dir: Path = Path("data/results"),
) -> Path:
    """Write a single-model YAML result for the provider.

    Emits exactly the T2 schema fields, in the documented order. A stable
    filename (<provider>.yaml) makes re-runs reproducible for diffing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "provider": provider,
        "model_id": record["provider_model_id"],
        "decision": record["decision"],
        "tier": record.get("tier", record.get("category")),
        "aa_model_id": record.get("aa_model_id"),
        "aa_score": record.get("aa_score"),
        "confidence": record["confidence"],
        "evidence_level": record.get("evidence_level"),
        "evidence": _clean_evidence(record.get("evidence", [])),
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


def save_provider_result(
    result: dict[str, list[dict[str, Any]]],
    provider: str,
    output_dir: Path = Path("data/results"),
) -> Path:
    """Write per-provider keep/drop/error YAML (idempotent — overwrites prior run).

    Each record is projected to a minimal, stable schema so the output diff is
    meaningful across runs. Errors are surfaced separately from drops.

    Drop is split into:
    - drop_llm: models dropped by LLM evaluation (coding=false, low score, etc.)


    Provider-level errors (e.g. HTTP 404 during discovery) are preserved with
    a ``stage`` field so users can distinguish discovery failures from
    evaluation failures.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    def _project(rec: dict[str, Any]) -> dict[str, Any]:
        projected: dict[str, Any] = {
            "model_id": rec["provider_model_id"],
            "decision": rec["decision"],
            "tier": rec.get("tier", rec.get("category")),
            "aa_model_id": rec.get("aa_model_id"),
            "aa_score": rec.get("aa_score"),
            "coding_score": rec.get("coding_score"),
            "benchmarks": rec.get("benchmarks"),
            "confidence": rec["confidence"],
            "evidence_level": rec.get("evidence_level"),
            "evidence": _clean_evidence(rec.get("evidence", [])),
            "coding_assessment": rec.get("coding_assessment"),
        }
        # Preserve stage info for provider-level errors.
        if "stage" in rec:
            projected["stage"] = rec["stage"]
        return projected

    timestamp = datetime.now(UTC).isoformat()

    # Collect LLM evaluation drops (free-model rule drops excluded from output)
    drop_llm = []
    for r in result.get("drop", []):
        projected = _project(r)
        evidence_str = " ".join(r.get("evidence", []))
        if "free-model-rule" not in evidence_str:
            drop_llm.append(projected)

    payload = {
        "provider": provider,
        "evaluated_at": timestamp,
        "keep": [_project(r) for r in result.get("keep", [])],
        "drop_llm": drop_llm,
        "error": [_project(r) for r in result.get("error", [])],
    }

    path = output_dir / f"{provider}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))

    return path


def save_all_providers_result(
    all_results: dict[str, dict[str, list[dict[str, Any]]]],
    output_dir: Path = Path("data/results"),
) -> list[Path]:
    """Write one YAML per provider (idempotent — overwrites prior runs)."""
    paths: list[Path] = []
    for provider, result in all_results.items():
        paths.append(save_provider_result(result, provider, output_dir))
    return paths

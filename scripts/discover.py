#!/usr/bin/env python3
"""LLM model discovery CLI.

Usage:
    .venv/bin/python scripts/discover.py <provider>        # T2: one model (tracer)
    .venv/bin/python scripts/discover.py <provider> --all   # T3: all models, parallel
    .venv/bin/python scripts/discover.py --all-providers    # all providers, parallel

No provider or API key is hardcoded.  The judge + provider keys come from the
user's local Infisical / environment — never requested by this script.

Env vars:
    AGNES_AI_API_KEY   Judge LLM key (loaded via Infisical at runtime).
    GROQ_API_KEY       Provider key (loaded via Infisical at runtime).
    BRAVE_API_KEY      Optional web search (loaded via Infisical).
    ENABLE_WEB_SEARCH=1  Enable web search (default: off, deterministic facts only).
"""
import argparse
import sys
from pathlib import Path

from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.config import load_config
from llm_discovery.pipeline import discover_single, discover_provider, discover_all_providers
from llm_discovery.results import save_yaml_result, save_provider_result

DATA_DIR = Path("data")


def parse_args(argv: list[str], config) -> argparse.Namespace:
    configured = [p.name for p in config.providers]
    parser = argparse.ArgumentParser(
        prog="discover.py",
        description="Discover and evaluate LLM providers.",
    )
    parser.add_argument(
        "provider",
        nargs="?",
        default=(configured[0] if configured else None),
        help=f"Provider to evaluate (default: first in config — {configured or 'none'}).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate ALL models for the provider (T3 batch), in parallel.",
    )
    parser.add_argument(
        "--all-providers",
        action="store_true",
        help="Evaluate ALL configured providers, each in parallel.",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=4,
        help="Max parallel judge calls per provider (default: 4).",
    )
    args = parser.parse_args(argv)

    if args.all_providers:
        return args

    if args.provider is None:
        raise SystemExit(
            "No provider specified and no providers configured in "
            "config/providers.yaml."
        )
    if args.provider not in configured:
        raise SystemExit(
            f"Provider {args.provider!r} not found in config/providers.yaml. "
            f"Configured providers: {configured}."
        )
    return args


def _print_record(rec: dict, label: str = "  ") -> None:
    """Pretty-print one model evaluation record."""
    decision = rec["decision"]
    tier = rec.get("tier", rec.get("category", "—"))
    score = rec.get("aa_score")
    score_str = f"  AA={score}" if score is not None else ""
    print(f"{label}{decision.upper():4} {tier:5} {rec['provider_model_id']}{score_str}")
    for ev in rec.get("evidence", []):
        print(f"{label}       └─ {ev[:100]}")


def main() -> None:
    config = load_config()
    args = parse_args(sys.argv[1:], config)

    aa = ArtificialAnalysisCatalog(DATA_DIR / "artificial_analysis_models.json")
    models_dev = ModelsDevCatalog(DATA_DIR / "models_dev_catalog.json")

    if args.all_providers:
        all_results = discover_all_providers(config, aa, models_dev, max_workers=args.workers)
        total_keep = sum(len(r["keep"]) for r in all_results.values())
        total_drop = sum(len(r["drop"]) for r in all_results.values())
        total_error = sum(len(r.get("error", [])) for r in all_results.values())
        print(f"\nProviders: {len(all_results)}  KEEP: {total_keep}  DROP: {total_drop}  ERROR: {total_error}")
        for provider, result in all_results.items():
            print(f"\n  [{provider}] keep={len(result['keep'])} drop={len(result['drop'])} error={len(result.get('error', []))}")
            for rec in result["keep"]:
                _print_record(rec)
            for rec in result["drop"]:
                _print_record(rec)
            for rec in result.get("error", []):
                _print_record(rec)
        return

    if args.all:
        result = discover_provider(args.provider, config, aa, models_dev, max_workers=args.workers)
        result_path = save_provider_result(result, args.provider)
        print(f"Saved: {result_path}")
        print(f"provider:    {args.provider}")
        print(f"KEEP:        {len(result['keep'])}")
        print(f"DROP:        {len(result['drop'])}")
        print(f"ERROR:       {len(result.get('error', []))}")
        print()
        for rec in result["keep"]:
            _print_record(rec)
        for rec in result["drop"]:
            _print_record(rec)
        for rec in result.get("error", []):
            _print_record(rec)
        return

    # Default: T2 tracer — single model
    record = discover_single(args.provider, config, aa, models_dev)
    result_path = save_yaml_result(record, args.provider)

    print(f"Saved: {result_path}")
    _print_record(record, label="")


if __name__ == "__main__":
    main()

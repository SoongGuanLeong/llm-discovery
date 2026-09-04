import argparse
from pathlib import Path

from .catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog


DATA_DIR = Path("data")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-discovery",
        description="Query LLM discovery catalogs.",
    )

    subparsers = parser.add_subparsers(dest="catalog", required=True)

    aa_parser = subparsers.add_parser("aa", help="Query Artificial Analysis.")
    aa_subparsers = aa_parser.add_subparsers(dest="command", required=True)

    search = aa_subparsers.add_parser("search", help="Search models.")
    search.add_argument("query")

    filter_parser = aa_subparsers.add_parser(
        "filter", help="Filter models by intelligence score."
    )
    filter_parser.add_argument("--min-score", type=float, default=25)

    models_parser = subparsers.add_parser(
        "models",
        help="Query models.dev models.",
    )
    models_subparsers = models_parser.add_subparsers(
        dest="command",
        required=True,
    )

    model = models_subparsers.add_parser("show", help="Show a model.")
    model.add_argument("model_id")

    providers = models_subparsers.add_parser(
        "providers",
        help="Show providers offering a model.",
    )
    providers.add_argument("model_id")

    provider_parser = subparsers.add_parser(
        "providers",
        help="Query models.dev providers.",
    )
    provider_subparsers = provider_parser.add_subparsers(
        dest="command",
        required=True,
    )

    provider = provider_subparsers.add_parser(
        "show",
        help="Show a provider.",
    )
    provider.add_argument("provider_id")

    provider_models = provider_subparsers.add_parser(
        "models",
        help="Show models offered by a provider.",
    )
    provider_models.add_argument("provider_id")

    refresh_parser = subparsers.add_parser("refresh", help="Refresh catalog snapshots (AA + models.dev + benchmarks).")
    refresh_parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Data directory (default: data)")
    refresh_parser.add_argument("--aa-url", default="https://artificialanalysis.ai/api/v2/data/llms/models", help="AA API URL")
    refresh_parser.add_argument("--models-dev-url", default="https://models.dev/catalog.json", help="models.dev catalog URL")
    refresh_parser.add_argument("--aa-api-key", default=None, help="AA API key (or env AA_API_KEY)")
    refresh_parser.add_argument("--no-backup", action="store_true", help="Disable .bak backup")
    refresh_parser.add_argument("--dry-run", action="store_true", help="Fetch and validate but do not write")
    refresh_parser.add_argument("--only", nargs="*", choices=["aa", "models_dev", "benchmarks"], help="Only refresh selected catalogs")

    build_parser = subparsers.add_parser("build-all", help="Build all providers into model_info_store (cache-optional, 14d filter, atomic).")
    build_parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Data directory (default: data)")
    build_parser.add_argument("--config", type=Path, default=Path("config/providers.yaml"), help="Providers YAML path")
    build_parser.add_argument("--providers", nargs="*", help="Optional subset of provider names")
    build_parser.add_argument("--max-workers", type=int, default=4, help="Workers per provider")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.catalog == "build-all":
        from .build_all import build_all
        try:
            res = build_all(
                data_dir=args.data_dir,
                config_path=args.config,
                provider_names=args.providers,
                max_workers=args.max_workers,
            )
            sp = res['store_path']
            sz = res['store_size']
            cb = res['compact_bytes']
            pb = res['pretty_bytes']
            print(f"Done: build-all store {sp} size={sz} compact {cb} < pretty {pb}")
        except Exception as e:
            print(f"build-all failed: {e}")
            raise SystemExit(1)
        return

    if args.catalog == "refresh":
        from .refresh import refresh_all
        import httpx
        try:
            results = refresh_all(
                data_dir=args.data_dir,
                aa_api_key=args.aa_api_key,
                aa_url=args.aa_url,
                models_dev_url=args.models_dev_url,
                backup=not args.no_backup,
                dry_run=args.dry_run,
                only=args.only,
            )
            print("Done:", ", ".join(f"{k}={v or 'dry-run'}" for k, v in results.items()))
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:500] if e.response is not None else ""
            print(f"HTTP {status} from {e.request.url if e.request else '?' }\n{body}")
            if status == 401:
                print("AA requires API key: set AA_API_KEY env or --aa-api-key")
            raise SystemExit(1)
        except SystemExit:
            raise
        except Exception as e:
            print(f"Refresh failed: {e}")
            raise SystemExit(1)
        return

    aa = ArtificialAnalysisCatalog(
        DATA_DIR / "artificial_analysis_models.json"
    )
    models_dev = ModelsDevCatalog(
        DATA_DIR / "models_dev_catalog.json"
    )

    if args.catalog == "aa":
        if args.command == "search":
            for model in aa.search(args.query):
                score = model["evaluations"].get(
                    "artificial_analysis_intelligence_index"
                )
                print(f"{model['name']} | {score}")

        elif args.command == "filter":
            models = aa.filter(min_score=args.min_score)

            for model in models:
                score = model["evaluations"][
                    "artificial_analysis_intelligence_index"
                ]
                print(f"{model['name']} | {score}")

    elif args.catalog == "models":
        if args.command == "show":
            model = models_dev.get_model(args.model_id)

            if model is None:
                parser.error(f"Model not found: {args.model_id}")

            print(model)

        elif args.command == "providers":
            providers = models_dev.providers_for_model(args.model_id)

            if not providers:
                parser.error(f"No providers found: {args.model_id}")

            for provider in providers:
                print(f"{provider['id']} | {provider['name']} | {provider['api']}")

    elif args.catalog == "providers":
        if args.command == "show":
            provider = models_dev.get_provider(args.provider_id)

            if provider is None:
                parser.error(f"Provider not found: {args.provider_id}")

            print(provider)

        elif args.command == "models":
            models = models_dev.models_for_provider(args.provider_id)

            if not models:
                parser.error(f"Provider not found or has no models: {args.provider_id}")

            for model_id, model in models.items():
                print(f"{model_id} | {model['name']}")


if __name__ == "__main__":
    main()

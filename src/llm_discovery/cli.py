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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

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

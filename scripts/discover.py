from pathlib import Path

from llm_discovery.catalogs import (
    ArtificialAnalysisCatalog,
    ModelsDevCatalog,
)
from llm_discovery.config import load_config
from llm_discovery.pipeline import discover_provider
from llm_discovery.results import save_result

DATA_DIR = Path("data")


def main() -> None:
    config = load_config()

    aa = ArtificialAnalysisCatalog(DATA_DIR / "artificial_analysis_models.json")

    models_dev = ModelsDevCatalog(DATA_DIR / "models_dev_catalog.json")

    result = discover_provider(
        "groq",
        config,
        aa,
        models_dev,
    )

    result_path = save_result(
        result,
        provider="groq",
    )

    print(f"Saved: {result_path}")

    print(f"KEEP:       {len(result['keep'])}")
    print(f"DROP:       {len(result['drop'])}")

    for item in result["keep"]:
        print(f"KEEP  {item['provider_model_id']} -> {item.get('aa_name') or item.get('canonical_name')}")

    for item in result["drop"]:
        print(f"DROP  {item['provider_model_id']} -> {item.get('aa_name') or item.get('canonical_name')}")


if __name__ == "__main__":
    main()

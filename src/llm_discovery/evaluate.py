from typing import Any

from .catalogs import ArtificialAnalysisCatalog
from .resolver import resolve_model


def evaluate_models(
    models: list[dict[str, Any]],
    aa: ArtificialAnalysisCatalog,
    min_score: float,
) -> dict[str, list[dict[str, Any]]]:
    result = {
        "keep": [],
        "drop": [],
        "unresolved": [],
    }

    for model in models:
        model_id = model["id"]
        resolution = resolve_model(model_id, aa)

        if resolution.aa_model is None:
            result["unresolved"].append(
                {
                    "provider_model_id": model_id,
                    "method": resolution.method,
                }
            )
            continue

        aa_model = resolution.aa_model
        score = aa_model["evaluations"].get("artificial_analysis_intelligence_index")

        evaluation = {
            "provider_model_id": model_id,
            "aa_name": aa_model["name"],
            "aa_id": aa_model["id"],
            "aa_slug": aa_model["slug"],
            "score": score,
            "method": resolution.method,
        }

        if score is not None and score >= min_score:
            result["keep"].append(evaluation)
        else:
            result["drop"].append(evaluation)

    return result

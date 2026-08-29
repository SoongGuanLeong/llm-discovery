import os
import time
from typing import Any

from .catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from .discovery import discover_models
from .evaluation import ModelEvaluationRequest
from .llm import LocalLLMEvaluator
from .provider import resolve_provider
from .search import TavilySearcher
from .secrets import load_discovery_secrets, load_shared_secrets


def discover_provider(
    provider_name: str,
    config: Any,
    aa: ArtificialAnalysisCatalog,
    models_dev: ModelsDevCatalog,
) -> dict[str, list[dict[str, Any]]]:
    provider_config = next(provider for provider in config.providers if provider.name == provider_name)

    provider = resolve_provider(provider_config, models_dev)

    load_shared_secrets(config.infisical)
    load_discovery_secrets(config.infisical)

    llm_api_key = os.environ.get(config.local_llm.secret)

    if not llm_api_key:
        raise RuntimeError(f"Missing API key environment variable: {config.local_llm.secret}")

    api_key = os.environ.get(provider.secret)

    if not api_key:
        raise RuntimeError(f"Missing API key environment variable: {provider.secret}")

    models = discover_models(provider.base_url, api_key)

    searcher = TavilySearcher(
        os.environ["TAVILY_API_KEY"],
    )

    load_discovery_secrets(config.infisical)

    evaluator = LocalLLMEvaluator(
        base_url=config.local_llm.base_url,
        model=config.local_llm.model,
        api_key=llm_api_key,
        min_score=config.artificial_analysis.min_score,
        search_web=searcher.search,
    )

    result = {
        "keep": [],
        "drop": [],
    }

    for model in models:
        model_id = model["id"]

        from .resolver import resolve_model

        resolution = resolve_model(model_id, aa)

        # Deterministic AA match: let Python provide the verified score
        # to the LLM, but still let the LLM make the final decision.
        aa_candidates = []

        if resolution.aa_model is not None:
            aa_model = resolution.aa_model
            score = aa_model["evaluations"].get("artificial_analysis_intelligence_index")

            aa_candidates.append(
                {
                    "id": aa_model["id"],
                    "name": aa_model["name"],
                    "slug": aa_model["slug"],
                    "score": score,
                }
            )

        request = ModelEvaluationRequest(
            provider=provider_name,
            model_id=model_id,
            provider_metadata=model,
            aa_candidates=aa_candidates,
        )

        try:
            llm_result = evaluator.evaluate(request)
        except Exception as exc:
            # Evaluation failure is an actual failure, not an
            # "unresolved model". Keep the pipeline deterministic.
            result["drop"].append(
                {
                    "provider_model_id": model_id,
                    "source": "llm_error",
                    "coding": False,
                    "decision": "drop",
                    "confidence": 0.0,
                    "evidence": [f"LLM evaluation failed: {exc}"],
                }
            )
            time.sleep(2)
            continue
        finally:
            time.sleep(2)

        evaluation = {
            "provider_model_id": model_id,
            "source": "llm",
            **llm_result.model_dump(),
        }

        # If the LLM identified an AA model, verify it against our
        # local AA catalog. Never trust an LLM-invented score.
        if llm_result.aa_model_id:
            aa_model = aa.get_by_id(llm_result.aa_model_id)

            if aa_model is not None:
                score = aa_model["evaluations"].get("artificial_analysis_intelligence_index")

                evaluation.update(
                    {
                        "aa_model_id": aa_model["id"],
                        "aa_name": aa_model["name"],
                        "aa_slug": aa_model["slug"],
                        "aa_score": score,
                    }
                )

        # Python remains the final authority for hard requirements.
        verified_score = evaluation.get("aa_score")

        if (
            not llm_result.coding
            or verified_score is not None
            and verified_score < config.artificial_analysis.min_score
        ):
            evaluation["decision"] = "drop"
            result["drop"].append(evaluation)
        elif llm_result.decision == "keep":
            result["keep"].append(evaluation)
        else:
            result["drop"].append(evaluation)
    return result

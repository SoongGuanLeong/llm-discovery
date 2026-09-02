"""Shared pytest fixtures for llm-discovery tests."""
import json
from pathlib import Path

import pytest

from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.config import AppConfig, load_config


AA_FIXTURE = {
    "source": "test",
    "models": [
        {
            "id": "aa-llama-3.3-70b-versatile",
            "name": "Llama 3.3 70B",
            "slug": "llama-3.3-70b-versatile",
            "evaluations": {
                "artificial_analysis_intelligence_index": 55.0,
                "artificial_analysis_coding_index": 80.0,
            },
        },
        {
            "id": "aa-llama-3.1-8b-instant",
            "name": "Llama 3.1 8B",
            "slug": "llama-3.1-8b-instant",
            "evaluations": {
                "artificial_analysis_intelligence_index": 35.0,
                "artificial_analysis_coding_index": 60.0,
            },
        },
        {
            "id": "aa-qwen-72b",
            "name": "Qwen 72B",
            "slug": "qwen-72b",
            "evaluations": {
                "artificial_analysis_intelligence_index": 15.0,
                "artificial_analysis_coding_index": 40.0,
            },
        },
    ],
}


MODELS_DEV_FIXTURE = {
    "models": {
        "llama-3.3-70b-versatile": {
            "id": "llama-3.3-70b-versatile",
            "name": "Llama 3.3 70B",
            "description": "Multilingual chat, reasoning, and coding model",
            "family": "llama",
            "tool_call": True,
            "modalities": {"input": ["text"], "output": ["text"]},
            "open_weights": True,
            "limit": {"context": 131072, "output": 32768},
        },
        "llama-3.1-8b-instant": {
            "id": "llama-3.1-8b-instant",
            "name": "Llama 3.1 8B",
            "description": "Compact Llama instruction model for fast chat",
            "family": "llama",
            "tool_call": True,
            "modalities": {"input": ["text"], "output": ["text"]},
            "open_weights": True,
            "limit": {"context": 131072, "output": 131072},
        },
        "allam-2-7b": {
            "id": "allam-2-7b",
            "name": "ALLaM-2-7b",
            "description": "ALLaM-2-7b instruction tuned model by SDAIA",
            "family": "allam",
            "tool_call": False,
            "modalities": {"input": ["text"], "output": ["text"]},
            "open_weights": True,
            "limit": {"context": 4096, "output": 4096},
        },
    },
    "providers": {
        "groq": {
            "id": "groq",
            "name": "Groq",
            "api": "https://api.groq.com/openai/v1",
            "env": ["GROQ_API_KEY"],
            "models": {
                "llama-3.3-70b-versatile": True,
                "llama-3.1-8b-instant": True,
                "allam-2-7b": True,
            },
        },
    },
}


@pytest.fixture()
def aa_catalog(tmp_path: Path) -> ArtificialAnalysisCatalog:
    path = tmp_path / "aa_models.json"
    path.write_text(json.dumps(AA_FIXTURE))
    return ArtificialAnalysisCatalog(path)


@pytest.fixture()
def models_dev(tmp_path: Path) -> ModelsDevCatalog:
    path = tmp_path / "models_dev.json"
    path.write_text(json.dumps(MODELS_DEV_FIXTURE))
    return ModelsDevCatalog(path)


@pytest.fixture()
def sample_models() -> list:
    return [
        {"id": "allam-2-7b", "owned_by": "groq", "object": "model"},
        {"id": "llama-3.3-70b-versatile", "owned_by": "groq", "object": "model"},
        {"id": "llama-3.1-8b-instant", "owned_by": "groq", "object": "model"},
    ]

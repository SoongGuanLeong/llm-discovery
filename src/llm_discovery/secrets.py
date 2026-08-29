import json
import os
import subprocess

from dotenv import load_dotenv

from .config import InfisicalConfig


def _load_project_secrets(
    project_id: str,
    environment: str,
) -> None:
    result = subprocess.run(
        [
            "infisical",
            "export",
            "--projectId",
            project_id,
            "--env",
            environment,
            "--format",
            "json",
            "--include-imports=false",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    secrets = json.loads(result.stdout)

    for secret in secrets:
        key = secret.get("key")
        value = secret.get("value")

        if key and value:
            os.environ.setdefault(key, value)


def load_shared_secrets(config: InfisicalConfig) -> None:
    load_dotenv()

    project_id = os.environ.get(config.shared_project_id_env)

    if not project_id:
        raise RuntimeError(f"Missing environment variable: {config.shared_project_id_env}")

    _load_project_secrets(project_id, config.environment)


def load_discovery_secrets(config: InfisicalConfig) -> None:
    load_dotenv()

    project_id = os.environ.get(config.discovery_project_id_env)

    if not project_id:
        raise RuntimeError(f"Missing environment variable: {config.discovery_project_id_env}")

    _load_project_secrets(project_id, config.environment)

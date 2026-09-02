from dataclasses import dataclass

from .catalogs import ModelsDevCatalog
from .config import ProviderConfig


@dataclass(frozen=True)
class ResolvedProvider:
    name: str
    base_url: str
    secret: str
    discovery: str = "openai"
    discovery_strategy: str | None = None


def resolve_provider(
    config: ProviderConfig,
    catalog: ModelsDevCatalog,
) -> ResolvedProvider:
    if config.base_url:
        return ResolvedProvider(
            name=config.name,
            base_url=config.base_url,
            secret=config.secret,
            discovery=config.discovery,
            discovery_strategy=config.discovery_strategy,
        )

    provider = catalog.get_provider(config.name)

    if provider is None:
        raise ValueError(
            f"Provider not found in models.dev: {config.name!r}. "
            "Specify base_url explicitly."
        )

    base_url = provider.get("api")

    if not base_url:
        raise ValueError(
            f"No API URL found for provider {config.name!r}. "
            "Specify base_url explicitly."
        )

    return ResolvedProvider(
        name=config.name,
        base_url=base_url,
        secret=config.secret,
        discovery=config.discovery,
        discovery_strategy=config.discovery_strategy,
    )

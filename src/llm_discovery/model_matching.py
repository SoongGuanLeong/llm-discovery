"""Model name matching with candidate retrieval and deterministic ranking.

Replaces binary exact/normalized matching with a hybrid approach:
1. Normalize provider model IDs
2. Generate candidates from catalogs using multiple similarity signals
3. Score candidates with weighted features
4. High confidence -> accept, medium -> LLM adjudication, low -> no match

Canonical resolution (T6): ModelMatcher.match() returns ModelResolution
directly carrying the resolved AA model dict, eliminating the extraction
loop previously duplicated in resolver.py and the re-derivation loop in
pipeline.py. resolver.py is now a thin re-export shim.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from difflib import SequenceMatcher


def normalize_model_id(value: str) -> str:
    """Normalize a model ID or slug to a comparable canonical form.

    Public API for model ID normalization. Tests and external callers should
    use this instead of the private _normalize helper.
    """
    value = value.lower().strip()
    value = value.rsplit("/", 1)[-1]
    # Strip free markers (:free, -free, _free, /free) before normalization
    value = re.sub(r"[:/_-]free$", "", value)
    # Strip nvidia- hyphen prefix to match AA slugs (nvidia/nemotron vs nvidia-nemotron)
    # Other providers (llama, minimax) keep their family prefix; only nvidia AA uses provider hyphen
    if value.startswith("nvidia-"):
        value = value[len("nvidia-"):]
    # Insert hyphen between letter and digit for multi-letter families (gemma4 -> gemma-4, not v2)
    value = re.sub(r"([a-z]{2,})(\d)", r"\1-\2", value)
    # Preserve dots inside version numbers: 2.5 -> zzzdotzzz -> restore after
    value = re.sub(r"(\d)\.(\d)", r"\1zzzdotzzz\2", value)
    value = re.sub(r"[^a-z0-9.]+", "-", value)
    value = value.replace("zzzdotzzz", ".")
    # Convert stray dots (not between digits) to hyphen - keep 2.5 dots only
    value = re.sub(r"(?<!\d)\.", "-", value)
    value = re.sub(r"\.(?!\d)", "-", value)
    value = re.sub(r"-+", "-", value)
    value = re.sub(r"-\.", ".", value)
    value = re.sub(r"\.-", ".", value)
    return value.strip("-.")


# Backward compatibility alias
_normalize = normalize_model_id


def _generate_match_variants(normalized: str) -> list[tuple[str, float, str]]:
    """Generate safe matching variants with confidence.
    Keeps original untouched, generates alternatives:
    - exact normalized (1.0)
    - version-format hyphen<->dot (0.95)
    - token reorder for claude families (0.90)
    """
    variants: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    def add(v: str, conf: float, reason: str):
        if v not in seen:
            seen.add(v)
            variants.append((v, conf, reason))
    add(normalized, 1.0, "exact_normalized")
    # Vendor suffix stripping (muse contributor, qwen -next) for versioned vendor aliases (issue #50)
    for suffix in ("-contributor", "-next"):
        if normalized.endswith(suffix):
            base = normalized[: -len(suffix)]
            add(base, 0.95, f"suffix_strip_{suffix[1:]}")
            # Also handle dot/hyphen version for stripped base (e.g., muse-spark-1.2 -> muse-spark-1-2)
            hyphen_variant = base.replace(".", "-")
            if hyphen_variant != base:
                add(hyphen_variant, 0.95, f"suffix_strip_{suffix[1:]}+version_format")
            dot_variant = re.sub(r"(\d)-(\d)", r"\1.\2", base)
            if dot_variant != base and dot_variant != hyphen_variant:
                add(dot_variant, 0.95, f"suffix_strip_{suffix[1:]}+version_format")
    hyphen_to_dot = re.sub(r"(\d)-(\d)", r"\1.\2", normalized)
    if hyphen_to_dot != normalized:
        add(hyphen_to_dot, 0.95, "version_format_variant")
    dot_to_hyphen = normalized.replace(".", "-")
    if dot_to_hyphen != normalized and dot_to_hyphen != hyphen_to_dot:
        add(dot_to_hyphen, 0.95, "version_format_variant")
    m = re.match(r"^(claude-(?:haiku|sonnet|opus))-(.+)$", normalized)
    if m:
        family = m.group(1)
        rest = m.group(2)
        suffix = family.split("-", 1)[1]
        reordered = f"claude-{rest}-{suffix}"
        add(reordered, 0.90, "token_reorder")
        reordered_dot = re.sub(r"(\d)-(\d)", r"\1.\2", reordered)
        if reordered_dot != reordered:
            add(reordered_dot, 0.90, "token_reorder+version_format")
    m2 = re.match(r"^claude-((?:\d+[.-]\d+).*?)-(haiku|sonnet|opus)$", normalized)
    if m2:
        version_part = m2.group(1)
        suffix = m2.group(2)
        reordered2 = f"claude-{suffix}-{version_part}"
        add(reordered2, 0.90, "token_reorder")
        r2_dot = re.sub(r"(\d)-(\d)", r"\1.\2", reordered2)
        if r2_dot != reordered2:
            add(r2_dot, 0.90, "token_reorder+version_format")
    return variants


@dataclass(frozen=True)
class ModelResolution:
    provider_model_id: str
    aa_model: dict[str, Any] | None
    method: str


@dataclass(frozen=True)
class ModelSignature:
    """Structured representation of a model identity."""
    provider: str
    family: str
    version: str
    variant: str = ""
    parameter_size: str = ""
    quantization: str = ""
    suffix: str = ""

    def __str__(self) -> str:
        parts = [self.provider, self.family, self.version]
        if self.variant:
            parts.append(self.variant)
        if self.parameter_size:
            parts.append(self.parameter_size)
        if self.quantization:
            parts.append(self.quantization)
        if self.suffix:
            parts.append(self.suffix)
        return "-".join(parts)


@dataclass
class CandidateMatch:
    """A candidate match with confidence breakdown."""
    catalog_model_id: str
    catalog_slug: str
    catalog_name: str
    confidence: float
    signals: dict[str, float] = field(default_factory=dict)
    signature_match: bool = False

    def __lt__(self, other: "CandidateMatch") -> bool:
        return self.confidence > other.confidence  # Higher confidence first


class ModelNormalizer:
    """Normalize model IDs for matching."""

    # Provider prefixes to strip
    PROVIDER_PREFIXES = (
        "nvidia/", "nemotron/", "llama/", "minimax/", "poolside/",
        "stepfun/", "thinkingmachines/", "meta/", "google/",
        "anthropic/", "openai/", "cohere/", "mistral/", "microsoft/",
        "databricks/", "together/", "deepseek/", "qwen/", "yi/",
        "inclusionai/", "liquid/", "dots-studio/", "nvidia-", "nemotron-",
        "llama-", "minimax-", "poolside-", "stepfun-", "thinkingmachines-",
        "meta-", "google-", "anthropic-", "openai-", "cohere-",
        "mistral-", "microsoft-", "databricks-", "together-", "deepseek-",
        "qwen-", "yi-", "inclusionai-", "liquid-", "dots-studio-"
    )

    # Suffixes to strip
    SUFFIX_PATTERNS = [
        r":(free|paid|beta|rc\d*)$",
        r"-(preview|beta|rc\d+)$",
        r"\s*\(.*?\)\s*",
    ]

    # Common name normalizations
    NAME_NORMALIZATIONS = {
        "minimax": "mm",
        "nemotron": "nemo",
        "laguna-s": "laguna",
        "laguna-xs": "laguna",
        "nemotron-3": "nemo-3",
        "nemotron-4": "nemo-4",
    }

    # Parameter size patterns (e.g., 550b, 120b, 7b, 30b, etc.)
    # Match number + 'b' at word boundary or end, but not 'a12b' style
    PARAM_PATTERN = re.compile(r"(\d+(?:\.\d+)?[bB])(?:[^a-z0-9]|$)")
    # Version pattern: major.minor.patch optionally with suffix, but not including variants like 'ultra'
    VERSION_PATTERN = re.compile(r"(\d+(?:[\.\-]\d+)+(?:-\w+)?)(?![a-z])")

    @classmethod
    def normalize(cls, model_id: str) -> str:
        """Normalize a model ID to a comparable form."""
        name = model_id.lower().strip()

        # Remove provider prefix (everything before last /)
        if "/" in model_id:
            name = model_id.split("/")[-1].lower().strip()
        else:
            name = re.sub(r"^.+?/", "", name)

        # Remove suffixes
        for pattern in cls.SUFFIX_PATTERNS:
            name = re.sub(pattern, "", name)

        # Normalize separators - preserve dots (for version numbers)
        name = re.sub(r"_", "-", name)
        name = re.sub(r"\s+", "-", name)
        name = re.sub(r"-+", "-", name)

        return name.strip("-")

    @classmethod
    def extract_signature(cls, model_id: str) -> ModelSignature:
        """Extract structured signature from model ID."""
        name = cls.normalize(model_id)

        # Extract parameter size first
        param_match = cls.PARAM_PATTERN.search(name)
        parameter_size = param_match.group(1).lower() if param_match else ""

        # Try to extract provider from original
        provider = ""
        if "/" in model_id:
            provider = model_id.split("/")[0].lower()
            provider = re.sub(r"[^a-z0-9]", "", provider)

        # If provider appears as prefix in normalized name, strip it
        # (e.g., AA catalog has "minimax/MiniMax-M3" -> normalized "minimax-m3")
        if provider and name.startswith(provider + "-"):
            name = name[len(provider) + 1:]

        # Remove parameter size from name for further parsing
        name_no_param = cls.PARAM_PATTERN.sub("", name).strip("-")

        # Extract version pattern using class pattern (excludes variants like 'ultra')
        version_match = cls.VERSION_PATTERN.search(name_no_param)
        version = version_match.group(1) if version_match else ""

        # Also handle single-letter family + digit version (e.g., "m3", "v3", "r1")
        # This is common for model series like Minimax M3, Nemotron 3, etc.
        if not version:
            single_version_match = re.search(r"^([a-z])(\d+)(?:[-_]|$)", name_no_param)
            if single_version_match:
                version = single_version_match.group(2)
                # The family is the letter
                family = single_version_match.group(1)
                # Remove the family+version from name
                name_no_version = name_no_param[single_version_match.end():].strip("-")
                variant = ""
            else:
                # Handle family-name-version pattern (e.g., "nemotron-3-ultra", "laguna-2.1")
                # Look for version after a family name (word followed by dash+version)
                family_version_match = re.search(r"^([a-z]+)-(\d+(?:\.\d+)?)(?:[-_]|$)", name_no_param)
                if family_version_match:
                    family = family_version_match.group(1)
                    version = family_version_match.group(2)
                    name_no_version = name_no_param[family_version_match.end():].strip("-")
                    variant = ""
                else:
                    name_no_version = name_no_param
        else:
            # Remove version from name
            name_no_version = (name_no_param[:version_match.start()] + name_no_param[version_match.end():]).strip("-")

        # If we didn't set family above, extract from remaining parts
        if not version or not 'family' in locals():
            parts = [p for p in name_no_version.split("-") if p]
            family = parts[0] if parts else ""
            variant = parts[1] if len(parts) > 1 else ""
        else:
            # If version was found via class pattern, extract family/variant from remaining
            parts = [p for p in name_no_version.split("-") if p]
            if not 'family' in locals():
                family = parts[0] if parts else ""
            if not 'variant' in locals():
                variant = parts[1] if len(parts) > 1 else ""

        return ModelSignature(
            provider=provider,
            family=family,
            version=version,
            variant=variant,
            parameter_size=parameter_size,
        )


class SimilarityScorer:
    """Score similarity between provider model and catalog model."""

    def __init__(self):
        self.weights = {
            "token_overlap": 0.30,
            "family_exact": 0.25,
            "version_exact": 0.20,
            "version_compatible": 0.10,
            "parameter_match": 0.10,
            "provider_match": 0.05,
        }

    def _tokenize(self, name: str) -> set[str]:
        """Split into meaningful tokens."""
        return set(t for t in re.split(r"[-_\.\s]+", name.lower()) if t)

    def _token_overlap(self, a: str, b: str) -> float:
        """Jaccard similarity of tokens."""
        tokens_a = self._tokenize(a)
        tokens_b = self._tokenize(b)
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    def _sequence_similarity(self, a: str, b: str) -> float:
        """SequenceMatcher ratio."""
        return SequenceMatcher(None, a, b).ratio()

    def _version_compatible(self, v1: str, v2: str) -> float:
        """Check if versions are compatible (e.g., 3.7.0 vs 3.7, 2.5 vs 2-5)."""
        if not v1 or not v2:
            return 0.0
        # Normalize hyphen and dot interchangeably for versions
        v1n = v1.replace("-", ".")
        v2n = v2.replace("-", ".")
        if v1n == v2n:
            return 1.0
        if v1 == v2:
            return 1.0
        # Check major.minor compatibility
        parts1 = v1n.split(".")
        parts2 = v2n.split(".")
        if len(parts1) >= 2 and len(parts2) >= 2:
            if parts1[0] == parts2[0] and parts1[1] == parts2[1]:
                return 0.8
            if parts1[0] == parts2[0]:
                return 0.5
        return 0.0

    def _parameter_match(self, p1: str, p2: str) -> float:
        """Check parameter size match."""
        if not p1 or not p2:
            return 0.0
        return 1.0 if p1 == p2 else 0.0

    def score(
        self,
        provider_norm: str,
        catalog_norm: str,
        provider_sig: ModelSignature,
        catalog_sig: ModelSignature,
    ) -> tuple[float, dict[str, float]]:
        """Compute similarity score with breakdown."""
        signals = {}

        # Token overlap (primary signal)
        signals["token_overlap"] = self._token_overlap(provider_norm, catalog_norm)

        # Family exact match (strong signal)
        signals["family_exact"] = 1.0 if (
            provider_sig.family and catalog_sig.family and
            provider_sig.family == catalog_sig.family
        ) else 0.0

        # Version exact match
        signals["version_exact"] = 1.0 if (
            provider_sig.version and catalog_sig.version and
            provider_sig.version == catalog_sig.version
        ) else 0.0

        # Version compatible
        signals["version_compatible"] = self._version_compatible(
            provider_sig.version, catalog_sig.version
        )

        # Parameter size match
        signals["parameter_match"] = self._parameter_match(
            provider_sig.parameter_size, catalog_sig.parameter_size
        )

        # Provider match (weak signal)
        signals["provider_match"] = 1.0 if (
            provider_sig.provider and catalog_sig.provider and
            provider_sig.provider == catalog_sig.provider
        ) else 0.0

        # Weighted sum
        confidence = sum(
            signals.get(k, 0.0) * w
            for k, w in self.weights.items()
        )

        return confidence, signals


class ModelMatcher:
    """Match provider models to catalog models with confidence scoring."""

    # Confidence thresholds
    HIGH_CONFIDENCE = 0.75
    MEDIUM_CONFIDENCE = 0.50
    LOW_CONFIDENCE = 0.50

    def __init__(
        self,
        aa_catalog: Optional[Any] = None,
        models_dev_catalog: Optional[Any] = None,
        benchmark_cache: Optional[Any] = None,
    ):
        self.aa_catalog = aa_catalog
        self.models_dev_catalog = models_dev_catalog
        self.benchmark_cache = benchmark_cache
        self.normalizer = ModelNormalizer()
        self.scorer = SimilarityScorer()

    @staticmethod
    def normalize_model_id(value: str) -> str:
        """Public API: normalize model ID via ModelMatcher."""
        return normalize_model_id(value)

    def _build_catalog_index(self) -> list[tuple[str, str, str, ModelSignature]]:
        """Build index of catalog models: (model_id, slug, name, signature)."""
        index = []

        # AA catalog
        if self.aa_catalog:
            for model in self.aa_catalog.models:
                model_id = model.get("id", "")
                slug = model.get("slug", "")
                name = model.get("name", "")
                if model_id:
                    sig = self.normalizer.extract_signature(model_id)
                    index.append((model_id, slug, name, sig))

        # models.dev catalog
        if self.models_dev_catalog:
            for model_id, model_data in self.models_dev_catalog.models.items():
                name = model_data.get("name", model_id)
                sig = self.normalizer.extract_signature(model_id)
                index.append((model_id, model_id, name, sig))

        # Benchmark cache
        if self.benchmark_cache and hasattr(self.benchmark_cache, "_data"):
            for key in self.benchmark_cache._data:
                sig = self.normalizer.extract_signature(key)
                index.append((key, key, key, sig))

        return index

    def find_candidates(
        self,
        provider_model_id: str,
        max_candidates: int = 10,
        min_score: float = 0.1,
    ) -> list[CandidateMatch]:
        """Find candidate matches for a provider model."""
        provider_norm = self.normalizer.normalize(provider_model_id)
        provider_sig = self.normalizer.extract_signature(provider_model_id)

        catalog = self._build_catalog_index()

        candidates: list[CandidateMatch] = []
        for model_id, slug, name, sig in catalog:
            catalog_norm = self.normalizer.normalize(model_id)
            confidence, signals = self.scorer.score(
                provider_norm, catalog_norm, provider_sig, sig
            )

            if confidence >= min_score:
                # Check if signature matches exactly
                signature_match = (
                    provider_sig.family == sig.family and
                    provider_sig.version == sig.version and
                    provider_sig.parameter_size == sig.parameter_size
                )

                candidates.append(CandidateMatch(
                    catalog_model_id=model_id,
                    catalog_slug=slug,
                    catalog_name=name,
                    confidence=confidence,
                    signals=signals,
                    signature_match=signature_match,
                ))

        candidates.sort()
        return candidates[:max_candidates]

    def match(
        self,
        provider_model_id: str,
        require_high_confidence: bool = True,
        **kwargs: Any,
    ) -> ModelResolution:
        """Resolve provider model ID to AA catalog entry.

        Returns ModelResolution carrying the resolved aa_model dict directly
        so callers need no second lookup loop (pipeline 116-124).
        Uses deterministic exact_slug / normalized_slug / unresolved logic.
        """
        if self.aa_catalog is None:
            return ModelResolution(
                provider_model_id=provider_model_id,
                aa_model=None,
                method="unresolved",
            )
        provider_slug = provider_model_id.rsplit("/", 1)[-1]
        # Strip date/release suffix like :0731, -0731, _0731 (4 digits) for base match
        base_slug = re.sub(r"[:/_-]\d{4}$", "", provider_slug)
        # DeepSeek: plain is old (0420), :0731 is new (0731)
        if provider_slug == "deepseek-v4-flash" and ":0731" not in provider_model_id and "0731" not in provider_slug:
            hit = [m for m in self.aa_catalog.models if m.get("slug") == "deepseek-v4-flash-0420"]
            if hit:
                return ModelResolution(provider_model_id=provider_model_id, aa_model=hit[0], method="alias_deepseek-old")
        if "0731" in provider_model_id or provider_slug == "deepseek-v4-flash-0731":
            hit = [m for m in self.aa_catalog.models if m.get("slug") == "deepseek-v4-flash"]
            if hit:
                return ModelResolution(provider_model_id=provider_model_id, aa_model=hit[0], method="alias_deepseek-new")
        # Strip free suffix before alias lookup so mimo-v2.5-free hits mimo-v2.5 entry (issue #50)
        stripped_slug = re.sub(r"[:/_-]free$", "", provider_slug, flags=re.IGNORECASE)
        stripped_base = re.sub(r"[:/_-]free$", "", base_slug, flags=re.IGNORECASE)
        alias_map = {
            "mimo-v2.5": "mimo-v2-5-0424",
            "mimo-v2-5": "mimo-v2-5-0424",
            "claude-haiku-4-5": "claude-4-5-haiku",
            "claude-haiku-4.5": "claude-4-5-haiku",
            "gemini-3.8-flash-high": "gemini-3-7-flash",
            # Vendor versioned aliases (issue #50): muse contributor, qwen -next
            "muse-spark-1.2-contributor": "muse-spark-1-2",
            "muse-spark-1.2": "muse-spark-1-2",
            "muse-spark-1-3-contributor": "muse-spark-1-2",
            "muse-spark-1.3-contributor": "muse-spark-1-2",
            "muse-spark-1-3": "muse-spark-1-2",
            "muse-spark-1.3": "muse-spark-1-2",
            "muse-spark-1-2-contributor": "muse-spark-1-2",
            "qwen-3.8-flash": "qwen3-8-flash-next",
            "qwen3.8-flash": "qwen3-8-flash-next",
            "qwen-3-8-flash": "qwen3-8-flash-next",
            "qwen3-8-flash": "qwen3-8-flash-next",
            # Mistral *-latest aliases (issue #42)
            "mistral-medium-latest": "mistral-medium-3-5",
            "mistral-large-latest": "mistral-large-3",
            "mistral-small-latest": "mistral-small-3-1",
            "pixtral-large-latest": "pixtral-large-2411",
            "magistral-medium-latest": "magistral-medium-2509",
            "magistral-small-latest": "magistral-small-2509",
        }
        for k, v in alias_map.items():
            if (provider_slug.lower() == k.lower() or base_slug.lower() == k.lower()
                or stripped_slug.lower() == k.lower() or stripped_base.lower() == k.lower()):
                hit = [m for m in self.aa_catalog.models if m.get("slug") == v]
                if hit:
                    return ModelResolution(provider_model_id=provider_model_id, aa_model=hit[0], method="alias_"+v)
        # Try exact on original slug only (base_slug after dated aliases to avoid dated->generic)
        exact = [m for m in self.aa_catalog.models if m.get("slug") == provider_slug]
        if len(exact) == 1:
            return ModelResolution(
                provider_model_id=provider_model_id,
                aa_model=exact[0],
                method="exact_slug",
            )
        # Dated Mistral aliases: provider dated variants -> versioned AA slug (before base_slug to avoid dated->generic)
        dated_alias_map = {
            "mistral-medium-2604": "mistral-medium-3-5",
            "mistral-medium-2505": "mistral-medium-3",
            "mistral-medium-2508": "mistral-medium-3-1",
            "mistral-large-2512": "mistral-large-3",
            "mistral-large-2411": "mistral-large-3",
            "mistral-small-2603": "mistral-small-4",
            "mistral-small-2506": "mistral-small-3-1",
            "mistral-small-2501": "mistral-small-3-1",
        }
        for k, v in dated_alias_map.items():
            if provider_slug.lower() == k.lower():
                hit = [m for m in self.aa_catalog.models if m.get("slug") == v]
                if hit:
                    return ModelResolution(provider_model_id=provider_model_id, aa_model=hit[0], method="alias_dated_"+v)
        # Generic dated fallback for Mistral family: any mistral-(medium|large|small)-YYYY -> best versioned (after exact)
        if re.match(r"^mistral-(medium|large|small)-\d{4}$", provider_slug, re.I):
            family = provider_slug.split("-")[1].lower()
            fallback = {
                "medium": "mistral-medium-3-5",
                "large": "mistral-large-3",
                "small": "mistral-small-4",
            }.get(family)
            if fallback:
                hit = [m for m in self.aa_catalog.models if m.get("slug") == fallback]
                if hit:
                    return ModelResolution(provider_model_id=provider_model_id, aa_model=hit[0], method="alias_dated_fallback_"+fallback)
        # Try exact on stripped base (dated suffix removed) - after dated aliases
        exact_base = [m for m in self.aa_catalog.models if m.get("slug") == base_slug]
        if len(exact_base) == 1:
            return ModelResolution(
                provider_model_id=provider_model_id,
                aa_model=exact_base[0],
                method="exact_slug_base",
            )
        # Normalized match (dot-preserving)
        for raw in (provider_model_id, provider_model_id.rsplit("/",1)[-1], base_slug):
            normalized = _normalize(raw)
            candidates = [
                m for m in self.aa_catalog.models if _normalize(m.get("slug", "")) == normalized
            ]
            if len(candidates) == 1:
                return ModelResolution(
                    provider_model_id=provider_model_id,
                    aa_model=candidates[0],
                    method="normalized_slug",
                )
            # Generate safe variants with confidence (ideal design: keep original untouched)
            for variant, conf, reason in _generate_match_variants(normalized):
                candidates2 = [
                    m for m in self.aa_catalog.models if _normalize(m.get("slug", "")) == variant
                ]
                if len(candidates2) == 1:
                    return ModelResolution(
                        provider_model_id=provider_model_id,
                        aa_model=candidates2[0],
                        method=f"normalized_variant_{reason}_{conf:.2f}",
                    )
        # Fallback: similarity scorer (ModelNormalizer + SequenceMatcher)
        try:
            candidates_scored = self.find_candidates(provider_model_id, max_candidates=5, min_score=0.50)
            if candidates_scored and candidates_scored[0].confidence >= self.HIGH_CONFIDENCE:
                best = candidates_scored[0]
                aa_model = next((m for m in self.aa_catalog.models if m.get("id") == best.catalog_model_id or m.get("slug") == best.catalog_slug), None)
                if aa_model:
                    return ModelResolution(
                        provider_model_id=provider_model_id,
                        aa_model=aa_model,
                        method=f"similarity_{best.confidence:.2f}",
                    )
            if candidates_scored and candidates_scored[0].confidence >= self.MEDIUM_CONFIDENCE:
                if len(candidates_scored) == 1 or (candidates_scored[0].confidence - candidates_scored[1].confidence) > 0.15:
                    best = candidates_scored[0]
                    aa_model = next((m for m in self.aa_catalog.models if m.get("id") == best.catalog_model_id or m.get("slug") == best.catalog_slug), None)
                    if aa_model:
                        return ModelResolution(
                            provider_model_id=provider_model_id,
                            aa_model=aa_model,
                            method=f"similarity_med_{best.confidence:.2f}",
                        )
        except Exception:
            pass
        return ModelResolution(
            provider_model_id=provider_model_id,
            aa_model=None,
            method="unresolved",
        )

    def match_with_adjudication(
        self,
        provider_model_id: str,
    ) -> tuple[Optional[CandidateMatch], list[CandidateMatch], str]:
        """
        Match with adjudication recommendation.

        Returns:
            - best_match: CandidateMatch or None
            - candidates: top candidates for LLM
            - recommendation: "accept" | "adjudicate" | "reject"
        """
        candidates = self.find_candidates(provider_model_id)
        if not candidates:
            return None, [], "reject"

        best = candidates[0]

        if best.confidence >= self.HIGH_CONFIDENCE:
            return best, candidates[:3], "accept"
        elif best.confidence >= self.MEDIUM_CONFIDENCE:
            return best, candidates[:5], "adjudicate"
        else:
            return None, candidates[:5], "reject"


def format_candidates_for_llm(candidates: list[CandidateMatch]) -> str:
    """Format candidates for LLM prompt."""
    if not candidates:
        return "No candidates found."

    lines = ["Candidates:"]
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"  {i}. {c.catalog_name} (id={c.catalog_model_id}, "
            f"slug={c.catalog_slug}, confidence={c.confidence:.2f})"
        )
    return "\n".join(lines)

def resolve_model(
    provider_model_id: str,
    aa: Any,
    models_dev: Any = None,
    benchmark_cache: Any = None,
) -> ModelResolution:
    """Module-level resolve_model for import compatibility.

    Delegates to ModelMatcher.match() so the extraction loop lives in exactly
    one place (ModelMatcher) and the caller already receives aa_model.
    """
    matcher = ModelMatcher(aa_catalog=aa, models_dev_catalog=models_dev, benchmark_cache=benchmark_cache)
    return matcher.match(provider_model_id)
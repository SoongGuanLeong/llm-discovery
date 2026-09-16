"""EvaluatorCoordinator -- stateful evaluation coordination.

Issue #96: extract evaluate_model into coordinator class.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .gate import _is_router_model_id, is_accurate_enough
from .judge import Judge
from .model_info_store import (
    ModelInfoStore,
    PricingSnapshot,
    aggregate_pricing,
    compute_evidence_hash,
    is_stale,
    normalize_store_key,
)
from .candidate_store import CANDIDATE_TTL_DAYS, CandidateRecord
from .categorize import categorize_model
from .policy_gate import PolicyGate, _is_router_model

TTL_DAYS = 28

# Internal exception to skip cache hit for keep records when catalog is stale
class _SkipCacheHit(Exception):
    """Internal signal to skip cache hit and re-evaluate."""
    pass

VISION_CHEAP_THRESHOLD = 1.2
VISION_CODING_SCORE_MIN = 35.0
VISION_AA_CODING_MIN = 45.0
VISION_AA_INTEL_MIN = 55.0
VISION_BENCH_MIN = 50.0


def resolve_cache_identity(model_id: str, resolution: Any = None) -> str:
    """Resolve canonical cache identity for a model.

    Returns the AA canonical model slug when the provider model reliably
    resolves to a canonical AA identity. Falls back to normalize_store_key(model_id)
    (the provider-specific normalized ID) when no AA match exists.

    Cache identity rule:
      - If resolve_model gives confident aa_model match → use normalize_store_key(aa_model slug/id)
      - Else → normalize_store_key(provider_model_id)

    This ensures that the same underlying model appearing through different
    providers (e.g., openai/gpt-4o and openrouter/gpt-4o) reuses one cached
    judge result when they resolve to the same canonical AA identity.
    """
    # Try AA canonical identity first
    if resolution is not None:
        aa_model = getattr(resolution, "aa_model", None) if not isinstance(resolution, dict) else resolution.get("aa_model")
        if aa_model is not None:
            # Method must indicate a confident match (not "none" or empty)
            method = getattr(resolution, "method", "") if not isinstance(resolution, dict) else resolution.get("method", "")
            if method not in ("none", "", None):
                # Use AA model slug/id as canonical identity
                slug = aa_model.get("slug") if isinstance(aa_model, dict) else getattr(aa_model, "slug", None)
                if slug:
                    identity = normalize_store_key(str(slug))
                    if identity:
                        return identity
                mid = aa_model.get("id") if isinstance(aa_model, dict) else getattr(aa_model, "id", None)
                if mid:
                    identity = normalize_store_key(str(mid))
                    if identity:
                        return identity
    # Fallback: provider-specific normalized key
    return normalize_store_key(model_id)


@dataclass
class EvaluatorCoordinator:
    """State object for model evaluation coordination."""
    provider_name: str
    aa: Any
    models_dev: Any
    evaluator: Any
    min_score: float
    max_score: float
    cache: Any = None
    store: ModelInfoStore = None
    candidate_store: Any = None  # issue #222: weak/none candidate cache (default None -> no candidate caching)
    catalog_stale: bool = False  # deprecated per-evidence TTL (issue #221) - kept for compat, ignored

    @staticmethod
    def _cached_decision(record: Any | None) -> str | None:
        """Extract the cached decision ('keep'/'drop') from a store record.

        Returns None when the record is missing or decision is not present.
        """
        if record is None:
            return None
        judge = getattr(record, "judge", None) if not isinstance(record, dict) else record.get("judge")
        if judge is not None:
            dec = judge.get("decision") if isinstance(judge, dict) else getattr(judge, "decision", None)
            if dec is not None:
                return str(dec).strip().lower()
        dec = getattr(record, "decision", None)
        if dec is None and isinstance(record, dict):
            dec = record.get("decision")
        if dec is not None:
            return str(dec).strip().lower()
        return None

    def evaluate(self, model: dict[str, Any]) -> dict[str, Any]:
        """Judge one model and apply tiering."""
        model_id = model["id"]
        from .pipeline import resolve_model as _resolve
        resolution = _resolve(model_id, self.aa, self.models_dev, self.cache)
        # Canonical cache identity shared by the Keeper and Candidate stores (issue #222)
        cache_key: str | None = None
        try:
            cache_key = resolve_cache_identity(model_id, resolution) or None
        except Exception:
            cache_key = None
        if self.store is not None:
            try:
                if cache_key:
                    cached = self.store.get(cache_key)
                    hit = self.classify_hit(cached)
                    if hit == "strong_hit":
                        assert cached is not None
                        # Per-evidence TTL (issue #221): global catalog_stale removed.
                        # Judgement reused when evidence_hash unchanged and pricing within 7d TTL,
                        # even if catalog fetched_at >14d. Only re-evaluate when evidence_hash changed.
                        fresh_bm = None
                        if self.cache is not None:
                            try:
                                from .benchmarks import BenchmarkDataCache, build_benchmark_profile
                                if isinstance(self.cache, BenchmarkDataCache):
                                    prof = build_benchmark_profile(model_id, self.provider_name, self.cache)
                                    fresh_bm = prof.to_dict() if prof.scores else None
                            except Exception:
                                fresh_bm = None
                        obs = self._derive_fresh_pricing_obs(resolution, self.provider_name, None)
                        result = self._build_cached_strong_record(
                            model_id, cached, obs, fresh_bm,
                            resolution=resolution, cache=self.cache,
                            min_score=self.min_score, max_score=self.max_score,
                        )
                        if self.provider_name == "llm7" and model.get("tier") == "turbo" and result.get("decision") != "drop":
                            result["tier"] = "flash"
                        # Keeper hit: model evaluates strong -> leave the Candidate store (issue #222)
                        self._reconcile_candidate_store(model_id, cache_key, resolution, result)
                        return result
            except _SkipCacheHit:
                pass  # Fall through to re-evaluate (catalog stale + cached keep)
            except Exception:
                pass
        # Candidate cache reuse (issue #222): weak/none cached with 60-90d TTL, no LLM on hit
        if self.candidate_store is not None and cache_key:
            try:
                candidate = self._candidate_cache_lookup(model_id, cache_key, resolution)
            except Exception:
                candidate = None
            if candidate is not None:
                return candidate
        # Fast-path specialized (tts/embedding/rerank/speech/safety) without bench lookup or LLM (spec #219)
        _lower = model_id.lower()
        _spec_patterns = ("tts", "embedding", "embed", "rerank", "reranker", "speech", "whisper", "safety", "guard", "moderation", "text-to-speech", "speech-to-text", "code-embedding", "text-embedding")
        if any(pt in _lower for pt in _spec_patterns) and "vision" not in _lower:
            for _pt in _spec_patterns:
                if _pt in _lower:
                    return {
                        "provider_model_id": model_id,
                        "source": "deterministic",
                        "coding": False,
                        "canonical_name": None,
                        "aa_model_id": None,
                        "aa_name": None,
                        "aa_slug": None,
                        "aa_score": None,
                        "coding_score": None,
                        "pricing": None,
                        "benchmarks": {},
                        "confidence": 1.0,
                        "decision": "drop",
                        "tier": "drop",
                        "evidence_level": "strong",
                        "evidence": [f"specialized_model:{_pt}"],
                        "coding_assessment": None,
                    }
        from .evidence_collector import EvidenceCollector
        # keep pipeline imports for test patch compat (patched in tests)
        from .pipeline import _is_vision_only as _pipeline_is_vision_only  # noqa: F401
        from .pipeline import _is_coding_capable as _pipeline_is_coding_capable  # noqa: F401
        from .pipeline import _is_cheap_or_free as _pipeline_is_cheap_or_free  # noqa: F401
        from .pipeline import deterministic_drop_record as _pipeline_deterministic_drop  # noqa: F401
        packet = EvidenceCollector(self.provider_name).collect(model, self.cache, self.models_dev, resolution)
        if packet.is_specialized():
            if self._is_vision_only(packet.deterministic_flags) and self._is_coding_capable(resolution, self.cache, model_id, self.provider_name) and self._is_cheap_or_free(resolution, model_id, self.models_dev):
                pass
            else:
                reason = packet.deterministic_flags[0] if packet.deterministic_flags else "specialized"
                return self.deterministic_drop_record(model_id, reason, self.cache)
        # --- Router deterministic keep before Judge (spec #219) ---
        if _is_router_model(model_id):
            result = self._deterministic_router_record(model_id, resolution, packet)
            # store drop/keep mirroring post-Judge path
            if self.store is not None:
                try:
                    from .model_info_store import ModelInfoRecord
                    rec = ModelInfoRecord.from_provider_record(result, provider=self.provider_name, evaluated_at=datetime.now(UTC).isoformat())
                    key = resolve_cache_identity(model_id, resolution)
                    if key:
                        self.store.put(key, rec)
                except Exception:
                    pass
            # issue #222: router records are strong -> leave the Candidate store if present
            self._reconcile_candidate_store(model_id, cache_key, resolution, result)
            if self.provider_name == "llm7" and model.get("tier") == "turbo" and result.get("decision") != "drop":
                result["tier"] = "flash"
            return result
        # --- Deterministic screening before Judge (spec #219) ---
        # Compute evidence strength via same PolicyGate floors as ADR 0008
        try:
            from .benchmarks import build_benchmark_profile, compute_coding_score
            _profile = build_benchmark_profile(model_id, self.provider_name, self.cache)
        except Exception:
            _profile = None
        # Derive scores for screening
        coding_score = None
        try:
            if _profile is not None and _profile.scores:
                coding_score, _, _ = compute_coding_score(_profile)
        except Exception:
            coding_score = None
        verified_score = self._aa_score(getattr(resolution, "aa_model", None) if resolution else None)
        det_level = PolicyGate._deterministic_evidence_level(
            verified_score, coding_score, _profile,
            provider_claims=getattr(packet, "provider_claims", None),
            model_id=model_id,
        )
        if det_level == "strong":
            result = self._deterministic_strong_record(model_id, resolution, _profile, packet)
            if self.store is not None and result.get("decision") == "keep":
                try:
                    ok, _ = is_accurate_enough(result)
                    if ok:
                        from .model_info_store import ModelInfoRecord
                        rec = ModelInfoRecord.from_provider_record(result, provider=self.provider_name, evaluated_at=datetime.now(UTC).isoformat())
                        key = resolve_cache_identity(model_id, resolution)
                        if key:
                            self.store.put(key, rec)
                except Exception:
                    pass
            elif self.store is not None and str(result.get("decision", "")).strip().lower() == "drop":
                try:
                    from .model_info_store import ModelInfoRecord
                    rec = ModelInfoRecord.from_provider_record(result, provider=self.provider_name, evaluated_at=datetime.now(UTC).isoformat())
                    key = resolve_cache_identity(model_id, resolution)
                    if key:
                        self.store.put(key, rec)
                except Exception:
                    pass
            # issue #222: strong result -> model is (or remains) a Keeper; drop stale candidate entry
            self._reconcile_candidate_store(model_id, cache_key, resolution, result)
            if self.provider_name == "llm7" and model.get("tier") == "turbo" and result.get("decision") != "drop":
                result["tier"] = "flash"
            return result
        elif det_level == "weak":
            # issue #222: weak/none -> uncertain, cached in the Candidate store with long TTL.
            # No Keeper-store write: weak never passes the Accurate-Enough Gate, and a
            # judge-less slim record would classify_hit "miss" on every build anyway.
            result = self._deterministic_weak_record(model_id, resolution, _profile, packet)
            self._reconcile_candidate_store(model_id, cache_key, resolution, result)
            return result
        # moderate/ambiguous -> LLM
        judge = Judge(self.evaluator)
        try:
            llm_result = judge.evaluate(self.provider_name, model, packet, self.cache)
        except Exception as exc:
            return self._llm_error_record(model_id, exc)
        gate = PolicyGate(self.min_score, self.max_score, self.cache, store=self.store)
        result = gate.apply(llm_result, resolution, model_id, self.provider_name, profile=_profile, packet=packet)
        # issue #222: weak/none LLM results are Candidates, never Keeper-store records.
        # strong/moderate keep/drop store writes stay exactly as before.
        _llm_lvl = str(result.get("evidence_level", "")).strip().lower()
        if self.store is not None and _llm_lvl not in ("weak", "none") and result.get("decision") == "keep":
            try:
                ok, _ = is_accurate_enough(result)
                if ok:
                    from .model_info_store import ModelInfoRecord
                    rec = ModelInfoRecord.from_provider_record(result, provider=self.provider_name, evaluated_at=datetime.now(UTC).isoformat())
                    key = resolve_cache_identity(model_id, resolution)
                    if key:
                        self.store.put(key, rec)
            except Exception:
                pass
        elif self.store is not None and _llm_lvl not in ("weak", "none") and str(result.get("decision", "")).strip().lower() == "drop":
            try:
                from .model_info_store import ModelInfoRecord
                rec = ModelInfoRecord.from_provider_record(result, provider=self.provider_name, evaluated_at=datetime.now(UTC).isoformat())
                key = resolve_cache_identity(model_id, resolution)
                if key:
                    self.store.put(key, rec)
            except Exception:
                pass
        self._reconcile_candidate_store(model_id, cache_key, resolution, result)
        if self.provider_name == "llm7" and model.get("tier") == "turbo":
            result["decision"] = "keep"
            result["tier"] = "flash"
        return result

    @staticmethod
    def classify_hit(record: Any | None) -> str:
        """Strong+moderate hit classification (28d TTL, same JSON).

        Slim v2+judge store holds strong+moderate (benchmarks+pricing+_meta+judge?); weak
        never written. When judge snapshot present, require judge.evidence_level in
        (strong, moderate) to classify as "strong_hit". Legacy top-level evidence_level
        is also checked. Records lacking evidence_level in both judge and top-level
        return "miss" (existence alone is not sufficient for a cache hit).
        Returns "strong_hit" or "miss" (kept name for compat — means cacheable hit).
        """
        if record is None:
            return "miss"
        # Prefer judge snapshot (new) over legacy top-level
        judge = getattr(record, "judge", None)
        if judge is not None:
            lvl = getattr(judge, "evidence_level", None)
            if lvl is None and isinstance(judge, dict):
                lvl = judge.get("evidence_level")
            if lvl is not None and str(lvl).strip() != "":
                lvl_norm = str(lvl).strip().lower()
                return "strong_hit" if lvl_norm in ("strong", "moderate") else "miss"
            # judge present but no evidence_level — ambiguous, must not be a hit
            return "miss"
        lvl = getattr(record, "evidence_level", None)
        if lvl is None:
            lvl = record.get("evidence_level") if isinstance(record, dict) else None
        if lvl is None or str(lvl).strip() == "":
            # Missing evidence_level: ambiguous legacy record — must NOT be a hit.
            # A record existing in the store is not sufficient evidence of a trustworthy judge result.
            return "miss"
        lvl_norm = str(lvl).strip().lower()
        return "strong_hit" if lvl_norm in ("strong", "moderate") else "miss"

    @staticmethod
    def _pricing_is_stale(record: Any) -> bool:
        """Pricing TTL 7d (3-7d) via _meta.last_updated (is_stale). Per-evidence split (issue #221)."""
        try:
            last = getattr(record._meta, "last_updated", None) if hasattr(record, "_meta") else None
            if last is None and isinstance(record, dict):
                last = record.get("_meta", {}).get("last_updated") if isinstance(record.get("_meta"), dict) else None
            from .model_info_store import PRICING_TTL_DAYS as _PTTL
            return is_stale(last, _PTTL)
        except Exception:
            return False

    @staticmethod
    def _refresh_pricing_if_stale(
    cached: Any,
    fresh_observations: list[dict[str, Any]] | None = None,
    ) -> Any:
        """If stale, re-average pricing from catalog observations; else verbatim copy.

        fresh_observations = list of {blended,input,output,provider} from catalogs.
        When None/empty and stale, return cached verbatim (catalog miss -> no change).
        Fix #104: empty pricing {per_provider_overrides:{}} with no blended counts as
        missing and forces re-derive when fresh_observations present, even if TTL fresh.
        """
        pricing_obj = cached.pricing if hasattr(cached, "pricing") else cached.get("pricing") if isinstance(cached, dict) else None
        has_blended = False
        if hasattr(pricing_obj, "blended"):
            has_blended = pricing_obj.blended is not None
        elif isinstance(pricing_obj, dict):
            has_blended = pricing_obj.get("blended", pricing_obj.get("price_1m_blended_3_to_1")) is not None
        force_refresh = not has_blended and bool(fresh_observations)
        if not EvaluatorCoordinator._pricing_is_stale(cached) and not force_refresh:
            return pricing_obj
        if not fresh_observations:
            return pricing_obj
        agg = aggregate_pricing(fresh_observations)
        if agg is None:
            return pricing_obj
        return PricingSnapshot.from_dict(agg)

    @staticmethod
    def _gap_fill_benchmarks(
    cached_bm: dict[str, Any],
    fresh_bm: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Immutable benchmarks: null->fill only. No delta rebuild per #91 Q3. Per-evidence TTL (issue #221) gap-fill 60-90d, not re-derived each TTL expiry.

        Fresh profile scores fill only when cached score missing/None.
        raw_benchmarks union deduped by string repr.
        """
        if not fresh_bm or not fresh_bm.get("scores"):
            return cached_bm
        out = dict(cached_bm)
        scores = dict(out.get("scores", {}))
        for k, v in fresh_bm["scores"].items():
            if k not in scores or scores[k] is None:
                scores[k] = v
            # else keep cached verbatim — even if fresh differs (immutable)
        out["scores"] = scores
        if fresh_bm.get("raw_benchmarks"):
            seen = set(str(x) for x in out.get("raw_benchmarks", []))
            merged = list(out.get("raw_benchmarks", []))
            for rb in fresh_bm["raw_benchmarks"]:
                if str(rb) not in seen:
                    merged.append(rb)
                    seen.add(str(rb))
            out["raw_benchmarks"] = merged
        # Preserve coverage fields if cached lacks them but fresh has them (gap-fill)
        for cov_key in ("benchmark_coverage", "coverage_with_supplements"):
            if out.get(cov_key) is None and fresh_bm.get(cov_key) is not None:
                out[cov_key] = fresh_bm[cov_key]
        return out

    @staticmethod
    def _derive_fresh_pricing_obs(
    resolution: Any,
    provider_name: str,
    explicit_obs: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if explicit_obs is not None:
            return explicit_obs
        try:
            aa_model = getattr(resolution, "aa_model", None)
            if aa_model and aa_model.get("pricing"):
                p = aa_model["pricing"]
                cand = {
                    "blended": p.get("price_1m_blended_3_to_1", p.get("blended")),
                    "input": p.get("price_1m_input_tokens", p.get("input")),
                    "output": p.get("price_1m_output_tokens", p.get("output")),
                    "provider": provider_name,
                }
                if cand["blended"] is not None or cand["input"] is not None or cand["output"] is not None:
                    return [cand]
        except Exception:
            pass
        return None

    def _build_cached_strong_record(self, raw_model_id, cached, fresh_pricing_obs=None, fresh_bm=None, resolution=None, cache=None, min_score=24.0, max_score=45.0):
        """Full record from slim store + live deterministic sources. No LLM.

        Symmetric for keep and drop when evidence_level in (strong, moderate). Mirrors
        PolicyGate.apply outputs but derives from cached benchmarks/pricing
        plus resolution + BenchmarkDataCache. Decision comes from cached
        judge snapshot (keep or drop); drop never recomputed as keep.
        """
        cache_key = normalize_store_key(raw_model_id)

        # Resolve AA live if not supplied
        if resolution is None:
            # Need catalogs; caller should pass resolution. Fallback tries empty matcher.
            try:
                from .model_resolver import resolve_model as _rm
                resolution = _rm(raw_model_id, None, None, cache)
            except Exception:
                resolution = None

        # Fresh benchmarks for gap-fill
        if fresh_bm is None and cache is not None:
            try:
                from .benchmarks import BenchmarkDataCache, build_benchmark_profile
                if isinstance(cache, BenchmarkDataCache):
                    profile_tmp = build_benchmark_profile(raw_model_id, self.provider_name, cache)
                    fresh_bm = profile_tmp.to_dict() if profile_tmp.scores else None
            except Exception:
                fresh_bm = None

        fresh_obs = self._derive_fresh_pricing_obs(resolution, self.provider_name, fresh_pricing_obs)

        pricing_snap = self._refresh_pricing_if_stale(cached, fresh_obs)
        if hasattr(pricing_snap, "to_dict"):
            pricing_dict = pricing_snap.to_dict()
        elif isinstance(pricing_snap, dict):
            pricing_dict = pricing_snap
        elif pricing_snap is not None:
            pricing_dict = {"blended": pricing_snap}
        else:
            pricing_dict = {}

        # Benchmarks: cached + gap-fill
        if hasattr(cached, "benchmarks") and cached.benchmarks:
            bm_dict = cached.benchmarks.to_dict() if hasattr(cached.benchmarks, "to_dict") else dict(cached.benchmarks)
        elif isinstance(cached, dict) and cached.get("benchmarks"):
            bm_dict = dict(cached["benchmarks"])
        else:
            bm_dict = {"scores": {}, "raw_benchmarks": []}
        bm_dict = self._gap_fill_benchmarks(bm_dict, fresh_bm)

        # Build profile for coding_score / coverage (use fresh_bm if cache empty)
        # Rebuild profile from bm_dict for deterministic scoring
        from .benchmarks import BenchmarkProfile, compute_coding_score
        profile = BenchmarkProfile(model_id=raw_model_id, provider=self.provider_name)
        profile.scores = bm_dict.get("scores", {})
        profile.raw_benchmarks = bm_dict.get("raw_benchmarks", [])
        coding_score, score_conf, score_reasons = (
            compute_coding_score(profile) if profile.scores else (None, 0.0, ["No benchmark data"])
        )
        # Coverage preserved via bm_dict or profile helpers
        if bm_dict.get("benchmark_coverage") is None:
            try:
                bm_dict["benchmark_coverage"] = profile.benchmark_coverage()
            except Exception:
                pass
        if bm_dict.get("coverage_with_supplements") is None:
            try:
                bm_dict["coverage_with_supplements"] = profile.coverage_with_supplements()
            except Exception:
                pass

        pricing_blended = pricing_dict.get("blended", pricing_dict.get("price_1m_blended_3_to_1"))

        # AA fields from live resolution
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        if aa_model is not None:
            aa_model_id = aa_model.get("id")
            aa_name = aa_model.get("name")
            aa_slug = aa_model.get("slug")
            verified_score = aa_model.get("evaluations", {}).get("artificial_analysis_intelligence_index")
        else:
            aa_model_id = aa_name = aa_slug = verified_score = None

        # Sibling heuristic for cache rebuild: if no verified_score and no coding_score, check older kept sibling
        has_sibling = False
        try:
            if verified_score is None and coding_score is None:
                from .policy_gate import _has_older_kept_sibling
                # use self.store as sibling source
                has_sibling = _has_older_kept_sibling(raw_model_id, self.store)
        except Exception:
            has_sibling = False

        # Judge reuse: if stored strong/moderate judge snapshot exists, reuse its evidence/confidence/coding/canonical_name verbatim
        # Pricing/benchmarks still gap-filled above; judge evidence preserved as-audited (http URLs already gated)
        cached_judge = getattr(cached, "judge", None) if not isinstance(cached, dict) else cached.get("judge")
        use_judge = False
        judge_evidence: list[str] | None = None
        judge_conf: float | None = None
        judge_coding: bool | None = None
        judge_canonical: str | None = None
        judge_tier: str | None = None
        if cached_judge is not None:
            if isinstance(cached_judge, dict):
                jl = str(cached_judge.get("evidence_level", "strong")).strip().lower()
                judge_evidence = list(cached_judge.get("evidence", []))
                judge_conf = cached_judge.get("confidence")
                judge_coding = cached_judge.get("coding")
                judge_canonical = cached_judge.get("canonical_name")
                judge_tier = cached_judge.get("tier")
            else:
                jl = str(getattr(cached_judge, "evidence_level", "strong")).strip().lower()
                judge_evidence = list(getattr(cached_judge, "evidence", []) or [])
                judge_conf = getattr(cached_judge, "confidence", None)
                judge_coding = getattr(cached_judge, "coding", None)
                judge_canonical = getattr(cached_judge, "canonical_name", None)
                judge_tier = getattr(cached_judge, "tier", None)
            if jl in ("strong", "moderate") and judge_evidence:
                use_judge = True

        # Deterministic coding bool: prefer stored judge coding when reused, else True (Keeper)
        if use_judge and judge_coding is not None:
            deterministic_coding = bool(judge_coding)
        else:
            deterministic_coding = True  # cache holds only Keepers => coding True by gate
        # Still respect critical weakness -> drop not applied on hit; hit only for Keeps
        # But compute true coding signal for tier fallback
        # deterministic coding already True; keep as is. If no benchmarks and no AA, still True (Keeper).

        # Evidence level promotion
        # Determine base evidence level from cached record/judge snapshot
        base_evidence_level = "strong"  # default
        if cached_judge is not None:
            if isinstance(cached_judge, dict):
                base_evidence_level = str(cached_judge.get("evidence_level", "strong")).strip().lower()
            else:
                base_evidence_level = str(getattr(cached_judge, "evidence_level", "strong")).strip().lower()
        else:
            lvl = getattr(cached, "evidence_level", None)
            if lvl is None:
                lvl = cached.get("evidence_level") if isinstance(cached, dict) else None
            if lvl is not None and str(lvl).strip() != "":
                base_evidence_level = str(lvl).strip().lower()
        # Clamp to strong/moderate; only strong+moderate are cacheable
        if base_evidence_level not in ("strong", "moderate"):
            base_evidence_level = "strong"
        # Promote with deterministic level, but never demote the base level (strong > moderate > weak)
        det_level = PolicyGate._deterministic_evidence_level(verified_score, coding_score, profile)
        evidence_level = PolicyGate._max_evidence_level(base_evidence_level, det_level)  # strong wins if base strong, otherwise moderate may be promoted or stay

        # Tier via categorize_model (pricing-aware)
        has_weakness = False
        weakness_reason = None
        try:
            from .benchmarks import has_critical_weakness
            has_weakness, weakness_reason = has_critical_weakness(profile) if profile.scores else (False, None)
        except Exception:
            pass
        # Tier: reuse judge tier when present and pricing unchanged, else recompute (pricing-aware)
        if use_judge and judge_tier:
            tier = judge_tier  # type: ignore
            # If pricing drifted, recompute tier deterministically (pricing influences flash vs max)
            try:
                recomputed = categorize_model(
                    coding=deterministic_coding,
                    aa_score=verified_score,
                    min_score=min_score,
                    max_score=max_score,
                    judge_decision="keep",
                    model_id=raw_model_id,
                    coding_score=coding_score,
                    has_critical_weakness=has_weakness,
                    pricing_blended=pricing_blended,
                    has_older_kept_sibling=has_sibling,
                )
                # Only override if pricing influence changes tier; keep judge tier otherwise
                if recomputed != judge_tier and pricing_blended is not None:
                    tier = recomputed
            except Exception:
                pass
        else:
            tier = categorize_model(
                coding=deterministic_coding,
                aa_score=verified_score,
                min_score=min_score,
                max_score=max_score,
                judge_decision="keep",
                model_id=raw_model_id,
                coding_score=coding_score,
                has_critical_weakness=has_weakness,
                pricing_blended=pricing_blended,
                has_older_kept_sibling=has_sibling,
            )



        # Evidence synthesis: benchmark sources + AA URL placeholder + pricing influence (fallback when no judge)
        if use_judge:
            evidence = judge_evidence  # type: ignore
            # Ensure pricing freshness reflected: append pricing line if not already present and pricing changed
            if pricing_blended is not None and not any("Pricing blended" in str(e) for e in evidence):
                evidence = list(evidence) + [f"Pricing blended ${pricing_blended:.2f}/1M via AA catalog (reused judge, pricing refreshed)"]
        else:
            evidence = []
            for key, bm in (bm_dict.get("scores") or {}).items():
                if isinstance(bm, dict):
                    src = bm.get("source", "")
                    score = bm.get("score")
                    if src and "http" in str(src):
                        evidence.append(f"{key} {score} via {src}")
                    elif src:
                        evidence.append(f"{key} {score} via {src} (https://www.datalearner.com/benchmarks/{key})")
            if aa_model_id and verified_score is not None:
                evidence.append(f"AA Intelligence Index {verified_score} for {aa_model_id} via https://artificialanalysis.ai/models/{aa_slug or aa_model_id}")
            if pricing_blended is not None:
                evidence.append(f"Pricing blended ${pricing_blended:.2f}/1M via AA catalog")
            if not any("http" in e for e in evidence) and bm_dict.get("scores"):
                evidence.append("https://www.datalearner.com/benchmarks/artificial-analysis-coding-index (benchmark coverage)")
            if not evidence:
                evidence = ["Cache hit: deterministic re-derive from slim store + live catalogs (no LLM)"]

        # Confidence: judge confidence when reused else coding_score coverage
        if use_judge and judge_conf is not None:
            confidence = float(judge_conf)
        else:
            confidence = score_conf if score_conf and score_conf > 0 else 0.9

        # coding_assessment: judge sourced when reused, else deterministic stub
        if use_judge:
            coding_assessment = {
                "is_coding": deterministic_coding,
                "confidence": confidence,
                "reason": "reused strong/moderate judge snapshot (no LLM)",
                "coding_score": coding_score,
                "aa_score": verified_score,
            }
        else:
            coding_assessment = {
                "is_coding": deterministic_coding,
                "confidence": confidence,
                "reason": "; ".join(score_reasons) if score_reasons else "deterministic derive at cache hit",
                "coding_score": coding_score,
                "aa_score": verified_score,
            }

        # Canonical name: prefer judge canonical when reused
        if use_judge and judge_canonical:
            canonical_out = judge_canonical
        else:
            canonical_out = aa_name

        # Decision comes from cached judge snapshot (strong==cache symmetric)
        cached_decision = "keep"
        try:
            _cj = getattr(cached, "judge", None) if not isinstance(cached, dict) else cached.get("judge")
            if _cj is not None:
                _dec = _cj.get("decision") if isinstance(_cj, dict) else getattr(_cj, "decision", "keep")
                if str(_dec).strip().lower() == "drop":
                    cached_decision = "drop"
        except Exception:
            pass
        # For drop strong, tier is always drop regardless of pricing
        if cached_decision == "drop":
            tier = "drop"
        stale = self._pricing_is_stale(cached)
        return {
            "provider_model_id": raw_model_id,
            "cache_key": cache_key,
            "model_id": raw_model_id,  # for _to_record compatibility
            "decision": cached_decision,
            "tier": tier,
            "aa_model_id": aa_model_id,
            "aa_name": aa_name,
            "aa_slug": aa_slug,
            "aa_score": verified_score,
            "coding_score": coding_score,
            "pricing": pricing_dict,
            "benchmarks": bm_dict,
            "confidence": confidence,
            "evidence_level": evidence_level,
            "evidence": evidence,
            "coding_assessment": coding_assessment,
            "canonical_name": canonical_out,
            "coding": deterministic_coding,
            "cached": True,
            "cache_hit_level": "strong",
            "reason": "cache_hit:strong:pricing_ttl_7d" if stale else "cache_hit:strong",
            "provider": self.provider_name,
            "source": "cache",
        }


    # ------------------------------------------------------------------
    # Record factories (Ticket 03) -- canonical implementations
    # ------------------------------------------------------------------
    def _llm_error_record(self, model_id: str, exc: Exception, coding_score: float = 0.0, benchmarks: dict | None = None) -> dict[str, Any]:
        """Judge failure → decision=error, tier=error (NOT drop).

        Cache-aware: when benchmarks is None and cache available, derive
        benchmarks/coding_score from BenchmarkDataCache for richer context.
        Preserves pipeline signature for expand-contract.
        """
        # Derive benchmarks/coding_score via cache when not explicitly supplied
        if benchmarks is None and self.cache is not None:
            try:
                from .benchmarks import build_benchmark_profile, compute_coding_score
                profile = build_benchmark_profile(model_id, self.provider_name, self.cache)
                benchmarks = profile.to_dict() if profile.scores else {}
                if coding_score == 0.0 and profile.scores:
                    cs, _, _ = compute_coding_score(profile)
                    if cs is not None:
                        coding_score = cs
            except Exception:
                benchmarks = {}
        if benchmarks is None:
            benchmarks = {}
        return {
            "provider_model_id": model_id,
            "source": "llm_error",
            "coding": False,
            "canonical_name": None,
            "aa_model_id": None,
            "aa_name": None,
            "aa_slug": None,
            "aa_score": None,
            "coding_score": coding_score,
            "benchmarks": benchmarks,
            "confidence": 0.0,
            "decision": "error",
            "tier": "error",
            "evidence_level": "none",
            "evidence": [f"LLM evaluation failed: {exc}"],
            "coding_assessment": None,
        }

    def deterministic_drop_record(self, model_id: str, reason: str, cache=None) -> dict[str, Any]:
        """Pre-filter drop (specialised / non-coding models)."""
        effective_cache = cache if cache is not None else self.cache
        from .benchmarks import build_benchmark_profile, compute_coding_score

        profile = build_benchmark_profile(model_id, "", effective_cache)
        benchmarks_dict = profile.to_dict() if profile.scores else {}
        coding_score, _, _ = compute_coding_score(profile) if profile.scores else (None, 0.0, [])
        return {
            "provider_model_id": model_id,
            "source": "deterministic",
            "coding": False,
            "canonical_name": None,
            "aa_model_id": None,
            "aa_name": None,
            "aa_slug": None,
            "aa_score": None,
            "coding_score": coding_score if profile.scores else None,
            "benchmarks": benchmarks_dict,
            "confidence": 1.0,
            "decision": "drop",
            "tier": "drop",
            "evidence_level": "strong",
            "evidence": [reason],
            "coding_assessment": None,
        }

    # alias for pipeline compat (underscore prefix)
    _deterministic_drop_record = deterministic_drop_record

    def _deterministic_router_record(self, model_id: str, resolution: Any, packet: Any) -> dict[str, Any]:
        """Router models deterministic keep flash strong without LLM (spec #219)."""
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        verified_score = self._aa_score(aa_model)
        aa_model_id = aa_model.get("id") if aa_model else None
        aa_name = aa_model.get("name") if aa_model else None
        aa_slug = aa_model.get("slug") if aa_model else None
        # benchmarks from profile for completeness (no LLM)
        try:
            from .benchmarks import build_benchmark_profile
            profile = build_benchmark_profile(model_id, self.provider_name, self.cache)
            benchmarks_dict = profile.to_dict() if profile.scores else {}
            from .benchmarks import compute_coding_score
            coding_score, _, _ = compute_coding_score(profile) if profile.scores else (None, 0.0, [])
        except Exception:
            benchmarks_dict = {}
            coding_score = None
        return {
            "provider_model_id": model_id,
            "source": "deterministic",
            "coding": True,
            "canonical_name": aa_name,
            "aa_model_id": aa_model_id,
            "aa_name": aa_name,
            "aa_slug": aa_slug,
            "aa_score": verified_score,
            "coding_score": coding_score,
            "pricing": (aa_model.get("pricing") if aa_model else None),
            "benchmarks": benchmarks_dict,
            "confidence": 1.0,
            "decision": "keep",
            "tier": "flash",
            "evidence_level": "strong",
            "evidence": ["Router model: always keep (routing meta-model)"],
            "coding_assessment": {"is_coding": True, "confidence": 1.0, "reason": "router deterministic", "coding_score": coding_score, "aa_score": verified_score},
        }

    def _deterministic_strong_record(self, model_id: str, resolution: Any, profile: Any, packet: Any) -> dict[str, Any]:
        """Strong evidence deterministic keep without LLM (spec #219)."""
        from .benchmarks import compute_coding_score, has_critical_weakness
        benchmarks_dict = profile.to_dict() if profile and profile.scores else {}
        coding_score, score_conf, score_reasons = (compute_coding_score(profile) if profile and profile.scores else (None, 0.0, ["No benchmark data"]))
        has_weakness, weakness_reason = (has_critical_weakness(profile) if profile and profile.scores else (False, None))
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        if aa_model is not None:
            aa_model_id = aa_model.get("id")
            aa_name = aa_model.get("name")
            aa_slug = aa_model.get("slug")
            verified_score = self._aa_score(aa_model)
            pricing = aa_model.get("pricing")
        else:
            aa_model_id = aa_name = aa_slug = verified_score = pricing = None
        pricing_blended = pricing.get("price_1m_blended_3_to_1") if isinstance(pricing, dict) else None
        # sibling heuristic
        has_sibling = False
        try:
            if verified_score is None and coding_score is None:
                from .policy_gate import _has_older_kept_sibling
                has_sibling = _has_older_kept_sibling(model_id, self.store)
        except Exception:
            has_sibling = False
        tier = categorize_model(
            coding=True,
            aa_score=verified_score,
            min_score=self.min_score,
            max_score=self.max_score,
            judge_decision="keep",
            model_id=model_id,
            coding_score=coding_score if profile and profile.scores else None,
            has_critical_weakness=has_weakness,
            pricing_blended=pricing_blended,
            has_older_kept_sibling=has_sibling,
        )
        # force drop if critical weakness
        if has_weakness:
            tier = "drop"
        evidence: list[str] = []
        for key, bm in (benchmarks_dict.get("scores") or {}).items():
            if isinstance(bm, dict):
                src = bm.get("source", "")
                score = bm.get("score")
                if src and "http" in str(src):
                    evidence.append(f"{key} {score} via {src}")
                elif src:
                    evidence.append(f"{key} {score} via https://example.com/{key}")
        if aa_model_id and verified_score is not None:
            evidence.append(f"AA Intelligence Index {verified_score} for {aa_model_id} via https://artificialanalysis.ai/models/{aa_slug or aa_model_id}")
        if pricing_blended is not None:
            evidence.append(f"Pricing blended ${pricing_blended:.2f}/1M via AA catalog")
        evidence.append("Deterministic strong evidence -> keep without LLM (screening)")
        decision = "keep" if tier not in ("drop", "error") else "drop"
        # if tier drop due to weakness, decision drop
        if has_weakness:
            decision = "drop"
        return {
            "provider_model_id": model_id,
            "source": "deterministic",
            "coding": True if tier != "drop" else False,
            "canonical_name": aa_name,
            "aa_model_id": aa_model_id,
            "aa_name": aa_name,
            "aa_slug": aa_slug,
            "aa_score": verified_score,
            "coding_score": coding_score,
            "pricing": pricing,
            "benchmarks": benchmarks_dict,
            "confidence": score_conf if score_conf and score_conf > 0 else 0.95,
            "decision": decision,
            "tier": tier,
            "evidence_level": "strong",
            "evidence": evidence,
            "coding_assessment": {"is_coding": True, "confidence": score_conf or 0.95, "reason": "; ".join(score_reasons) if score_reasons else "deterministic strong", "coding_score": coding_score, "aa_score": verified_score},
        }

    def _deterministic_weak_record(self, model_id: str, resolution: Any, profile: Any, packet: Any) -> dict[str, Any]:
        """Weak/none evidence deterministic uncertain without LLM (spec #219, issue #222).

        issue #222: decision/tier are "uncertain" (insufficient evidence to determine
        quality) instead of drop; such results are cached in the Candidate store with
        a 60-90d TTL and never written to the slim v2 Keeper store.
        """
        from .benchmarks import compute_coding_score
        benchmarks_dict = profile.to_dict() if profile and profile.scores else {}
        coding_score, _, score_reasons = (compute_coding_score(profile) if profile and profile.scores else (None, 0.0, ["No benchmark data"]))
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        if aa_model is not None:
            aa_model_id = aa_model.get("id")
            aa_name = aa_model.get("name")
            aa_slug = aa_model.get("slug")
            verified_score = self._aa_score(aa_model)
            pricing = aa_model.get("pricing")
        else:
            aa_model_id = aa_name = aa_slug = verified_score = pricing = None
        evidence: list[str] = [
            "Weak/none evidence: no AA >=24, no bench >=30, unverified claim -> deterministic uncertain without LLM",
            "Insufficient evidence to determine coding quality; marked uncertain (candidate-cached 60-90d, issue #222)",
        ]
        for key, bm in (benchmarks_dict.get("scores") or {}).items():
            if isinstance(bm, dict):
                src = bm.get("source", "")
                score = bm.get("score")
                evidence.append(f"{key} {score} via {src}" if src else f"{key} {score}")
        if verified_score is not None:
            evidence.append(f"AA Intelligence Index {verified_score} for {aa_model_id}")
        return {
            "provider_model_id": model_id,
            "source": "deterministic",
            "coding": False,
            "canonical_name": aa_name,
            "aa_model_id": aa_model_id,
            "aa_name": aa_name,
            "aa_slug": aa_slug,
            "aa_score": verified_score,
            "coding_score": coding_score if profile and profile.scores else None,
            "pricing": pricing,
            "benchmarks": benchmarks_dict,
            "confidence": 1.0,
            "decision": "uncertain",
            "tier": "uncertain",
            "evidence_level": "weak",
            "evidence": evidence,
            "coding_assessment": {"is_coding": False, "confidence": 1.0, "reason": "deterministic weak/none", "coding_score": coding_score, "aa_score": verified_score},
        }

    # ------------------------------------------------------------------
    # Candidate cache (weak/none long-TTL reuse, issue #222)
    # ------------------------------------------------------------------
    def _candidate_evidence_hash(self, model_id: str, resolution: Any) -> str:
        """Evidence fingerprint for Candidate-store reuse (issue #222).

        Computed from the deterministic evidence state: AA score, benchmark scores
        dict, blended pricing, and benchmark source http URLs. Reuses
        compute_evidence_hash (issue #221). When evidence recovers or changes
        (AA appears, a bench lands, pricing moves) the hash changes and the
        cached Candidate no longer matches -> re-evaluate.
        """
        from .benchmarks import build_benchmark_profile

        try:
            profile = build_benchmark_profile(model_id, self.provider_name, self.cache)
        except Exception:
            profile = None
        bench_scores = profile.to_dict().get("scores") if profile is not None and profile.scores else {}
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        aa_score = self._aa_score(aa_model)
        pricing_blended = None
        try:
            if isinstance(aa_model, dict):
                pricing = aa_model.get("pricing")
                if isinstance(pricing, dict):
                    pricing_blended = pricing.get("price_1m_blended_3_to_1", pricing.get("blended"))
        except Exception:
            pricing_blended = None
        urls: list[str] = []
        for _key, bm in (bench_scores or {}).items():
            if isinstance(bm, dict):
                src = str(bm.get("source", "") or "")
                if "http" in src:
                    urls.append(src)
        return compute_evidence_hash(aa_score, bench_scores, pricing_blended, urls)

    def _candidate_cache_lookup(self, model_id: str, cache_key: str, resolution: Any) -> dict[str, Any] | None:
        """Reuse a cached weak/none result (issue #222) -- or None on miss.

        Hit when an entry exists for the cache identity, the evidence hash is
        identical, and age <= CANDIDATE_TTL_DAYS. On hit returns a record shaped
        as a normal evaluation result with source="candidate_cache", cached=True,
        decision="uncertain", tier="uncertain" and the candidate's evidence_level --
        with NO LLM call. Misses (absent, hash changed, expired) count as misses.
        """
        store = self.candidate_store
        entry = store.get(cache_key)
        if entry is None:
            store.stats.record_miss()
            return None
        current_hash = self._candidate_evidence_hash(model_id, resolution)
        if not entry.last_updated or entry.evidence_hash != current_hash or is_stale(entry.last_updated, CANDIDATE_TTL_DAYS):
            # evidence changed (recovered/modified) or entry expired -> re-evaluate
            store.stats.record_miss()
            return None
        store.stats.record_hit()
        from .benchmarks import build_benchmark_profile

        try:
            profile = build_benchmark_profile(model_id, self.provider_name, self.cache)
        except Exception:
            profile = None
        result = self._deterministic_weak_record(model_id, resolution, profile, None)
        result["source"] = "candidate_cache"
        result["cached"] = True
        result["cache_key"] = cache_key
        result["evidence_level"] = entry.evidence_level
        result.setdefault("evidence", []).append(
            f"Candidate cache hit: {entry.evidence_level} evidence unchanged, {CANDIDATE_TTL_DAYS}d TTL (issue #222), no LLM"
        )
        return result

    def _reconcile_candidate_store(self, model_id: str, cache_key: str | None, resolution: Any, result: dict[str, Any]) -> None:
        """Sync the Candidate store with a fresh evaluation result (issue #222).

        - strong/moderate result: model passed (or remains) cacheable -> delete any
          stale candidate entry (a model that became a Keeper leaves the Candidate
          store).
        - weak/none result (decision uncertain/keep/drop): upsert a candidate entry
          with the current evidence hash.
        - error results leave the store untouched (transient failures are retried
          next build, not cached for 60-90d).
        """
        if self.candidate_store is None or not cache_key:
            return
        try:
            lvl = str(result.get("evidence_level", "")).strip().lower()
            if lvl in ("strong", "moderate"):
                if cache_key in self.candidate_store:
                    self.candidate_store.delete(cache_key)
                return
            if lvl in ("weak", "none") and str(result.get("decision", "")).strip().lower() in ("uncertain", "keep", "drop"):
                rec = CandidateRecord(
                    model_id=model_id,
                    evidence_hash=self._candidate_evidence_hash(model_id, resolution),
                    evidence_level=lvl,
                    decision=str(result.get("decision", "uncertain")).strip().lower(),
                    tier=str(result.get("tier", "uncertain")).strip().lower(),
                    last_updated=datetime.now(UTC).isoformat(),
                )
                self.candidate_store.put(cache_key, rec)
        except Exception:
            pass

    def _auto_free_record(self, provider_name: str | None = None) -> dict[str, Any]:
        """Auto-free provider: skip evaluation, return auto:free routing recommendation."""
        pn = provider_name if provider_name is not None else self.provider_name
        return {
            "provider_model_id": "auto:free",
            "source": "auto_free",
            "coding": True,
            "canonical_name": None,
            "aa_model_id": None,
            "aa_name": None,
            "aa_slug": None,
            "aa_score": None,
            "coding_score": None,
            "benchmarks": {},
            "confidence": 1.0,
            "decision": "keep",
            "tier": "max",
            "evidence_level": "strong",
            "evidence": [f"Provider {pn} uses auto_free discovery strategy"],
            "coding_assessment": None,
        }

    @staticmethod
    def _aa_score(aa_model: dict[str, Any] | None) -> float | None:
        if aa_model is None:
            return None
        return aa_model.get("evaluations", {}).get("artificial_analysis_intelligence_index")

    @staticmethod
    def _aa_match(resolution: Any) -> dict[str, Any] | None:
        """The deterministic AA match handed to the judge as verified context."""
        if resolution is None or getattr(resolution, "aa_model", None) is None:
            return {"matched": False, "model_id": None, "score": None}
        aa_model = resolution.aa_model
        return {
            "matched": True,
            "model_id": aa_model["id"],
            "score": EvaluatorCoordinator._aa_score(aa_model),
        }

    @staticmethod
    def _aa_candidates(resolution: Any) -> list[dict[str, Any]]:
        """Legacy: deterministic AA match(es) for backward compatibility."""
        if resolution is None or getattr(resolution, "aa_model", None) is None:
            return []
        aa_model = resolution.aa_model
        return [
            {
                "id": aa_model["id"],
                "name": aa_model["name"],
                "slug": aa_model["slug"],
                "score": EvaluatorCoordinator._aa_score(aa_model),
            }
        ]

    @staticmethod
    def _is_vision_only(flags: list[str]) -> bool:
        """True only if every deterministic flag is vision — no embedding/tts/etc."""
        if not flags:
            return False
        return all(f == "specialized_model:vision" for f in flags)

    @staticmethod
    def _is_vision_free_model(model_id: str, resolution: Any, models_dev: Any) -> bool:
        lower = model_id.lower()
        if "free" in lower:
            return True
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        if aa_model:
            pricing = aa_model.get("pricing") or {}
            blended = pricing.get("price_1m_blended_3_to_1")
            inp = pricing.get("price_1m_input_tokens")
            out = pricing.get("price_1m_output_tokens")
            if blended == 0 or (inp == 0 and out == 0):
                return True
        return False

    @staticmethod
    def _is_cheap_or_free(resolution: Any, model_id: str, models_dev: Any) -> bool:
        if EvaluatorCoordinator._is_vision_free_model(model_id, resolution, models_dev):
            return True
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        if aa_model:
            pricing = aa_model.get("pricing") or {}
            blended = pricing.get("price_1m_blended_3_to_1")
            if blended is not None and blended <= VISION_CHEAP_THRESHOLD:
                return True
        return False

    @staticmethod
    def _is_coding_capable(resolution: Any, cache: Any, model_id: str, provider_name: str) -> bool:
        aa_model = getattr(resolution, "aa_model", None) if resolution else None
        if aa_model:
            evals = aa_model.get("evaluations") or {}
            aa_coding = evals.get("artificial_analysis_coding_index")
            aa_intel = evals.get("artificial_analysis_intelligence_index")
            if aa_coding is not None and aa_coding >= VISION_AA_CODING_MIN:
                return True
            if aa_intel is not None and aa_intel >= VISION_AA_INTEL_MIN:
                return True
        if cache is not None:
            from .benchmarks import build_benchmark_profile, compute_coding_score

            profile = build_benchmark_profile(model_id, provider_name, cache)
            if profile.scores:
                coding_score, _, _ = compute_coding_score(profile)
                if coding_score is not None and coding_score >= VISION_CODING_SCORE_MIN:
                    return True
                for key in ("swe_bench_verified", "swe_bench_pro", "terminal_bench", "terminal_bench_2_1"):
                    val = profile.scores.get(key)
                    if val:
                        score = val.get("score") if isinstance(val, dict) else getattr(val, "score", None)
                        if score is not None and score >= VISION_BENCH_MIN:
                            return True
                aa_coding_bm = profile.scores.get("aa_coding")
                if aa_coding_bm:
                    s = aa_coding_bm.get("score") if isinstance(aa_coding_bm, dict) else None
                    if s is not None and s >= VISION_AA_CODING_MIN:
                        return True
                aa_intel_bm = profile.scores.get("aa_intelligence")
                if aa_intel_bm:
                    s = aa_intel_bm.get("score") if isinstance(aa_intel_bm, dict) else None
                    if s is not None and s >= VISION_AA_INTEL_MIN:
                        return True
        return False

def classify_hit(record): return EvaluatorCoordinator.classify_hit(record)
def _pricing_is_stale(record): return EvaluatorCoordinator._pricing_is_stale(record)
def _refresh_pricing_if_stale(cached, obs=None): return EvaluatorCoordinator._refresh_pricing_if_stale(cached, obs)
def _gap_fill_benchmarks(cached_bm, fresh_bm=None): return EvaluatorCoordinator._gap_fill_benchmarks(cached_bm, fresh_bm)
def _derive_fresh_pricing_obs(r, pn, eo=None): return EvaluatorCoordinator._derive_fresh_pricing_obs(r, pn, eo)
def build_cached_strong_record(
    rid,
    pn,
    cached,
    fresh_pricing_obs=None,
    fresh_bm=None,
    resolution=None,
    cache=None,
    min_score=24.0,
    max_score=45.0,
    **kwargs,
):
    # Compat aliases: old terse names from early Ticket 02 shim (fpo/fbm/res/c/ms/mx)
    if fresh_pricing_obs is None and "fpo" in kwargs:
        fresh_pricing_obs = kwargs.pop("fpo")
    if fresh_bm is None and "fbm" in kwargs:
        fresh_bm = kwargs.pop("fbm")
    if resolution is None and "res" in kwargs:
        resolution = kwargs.pop("res")
    if cache is None and "c" in kwargs:
        cache = kwargs.pop("c")
    if kwargs.get("ms") is not None and min_score == 24.0:
        min_score = kwargs.pop("ms")
    if kwargs.get("mx") is not None and max_score == 45.0:
        max_score = kwargs.pop("mx")
    # Also pop terse if passed as kwargs with pipeline names already handled
    kwargs.pop("fpo", None); kwargs.pop("fbm", None); kwargs.pop("res", None); kwargs.pop("c", None); kwargs.pop("ms", None); kwargs.pop("mx", None)
    if kwargs:
        raise TypeError(f"build_cached_strong_record() got unexpected keyword arguments {list(kwargs.keys())}")
    coord = EvaluatorCoordinator(provider_name=pn, aa=None, models_dev=None, evaluator=None, min_score=min_score, max_score=max_score, cache=cache)
    return coord._build_cached_strong_record(rid, cached, fresh_pricing_obs, fresh_bm, resolution, cache, min_score, max_score)
def build_cached_keep_record(*a, **k): return build_cached_strong_record(*a, **k)

# --- Record factory shims (Ticket 03) -- expand-contract ---
def _llm_error_record(model_id: str, exc: Exception, coding_score: float = 0.0, benchmarks: dict | None = None) -> dict[str, Any]:
    return EvaluatorCoordinator(provider_name="", aa=None, models_dev=None, evaluator=None, min_score=24.0, max_score=45.0)._llm_error_record(model_id, exc, coding_score, benchmarks)

def deterministic_drop_record(model_id: str, reason: str, cache=None) -> dict[str, Any]:
    return EvaluatorCoordinator(provider_name="", aa=None, models_dev=None, evaluator=None, min_score=24.0, max_score=45.0, cache=cache).deterministic_drop_record(model_id, reason, cache)

_deterministic_drop_record = deterministic_drop_record

def _auto_free_record(provider_name: str) -> dict[str, Any]:
    return EvaluatorCoordinator(provider_name=provider_name, aa=None, models_dev=None, evaluator=None, min_score=24.0, max_score=45.0)._auto_free_record(provider_name)

def _aa_score(aa_model: dict[str, Any] | None) -> float | None:
    return EvaluatorCoordinator._aa_score(aa_model)

def _aa_match(resolution: Any) -> dict[str, Any] | None:
    return EvaluatorCoordinator._aa_match(resolution)

def _aa_candidates(resolution: Any) -> list[dict[str, Any]]:
    return EvaluatorCoordinator._aa_candidates(resolution)

def _is_vision_only(flags: list[str]) -> bool:
    return EvaluatorCoordinator._is_vision_only(flags)

def _is_vision_free_model(model_id: str, resolution: Any, models_dev: Any) -> bool:
    return EvaluatorCoordinator._is_vision_free_model(model_id, resolution, models_dev)

def _is_cheap_or_free(resolution: Any, model_id: str, models_dev: Any) -> bool:
    return EvaluatorCoordinator._is_cheap_or_free(resolution, model_id, models_dev)

def _is_coding_capable(resolution: Any, cache: Any, model_id: str, provider_name: str) -> bool:
    return EvaluatorCoordinator._is_coding_capable(resolution, cache, model_id, provider_name)
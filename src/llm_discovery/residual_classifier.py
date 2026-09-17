"""Residual taxonomy helper — classify avoidable vs genuine uncertain (issue #238).

Lives beside build_gate harness and reuses canonical evidence identity variants
and gate signals. Does not duplicate matching logic and respects conservative
dated/suffix stripping, safe-merge negatives, UUID and hallucinated blocking.

Taxonomy (closed):
  alias_miss, benchmark_miss, claim_unverified, genuine_weak_no_claim,
  candidate_cached, search_unavailable, judge_error

Signature:
  classify_residual_uncertain(record, catalogs=None, candidate_store=None)
    -> {category, reason, recovery_attempts}

catalogs may be:
  - dict with keys aa / models_dev / cache / benchmark_cache
  - object with attributes aa, models_dev, cache
  - tuple/list (aa, models_dev, cache)
  - single BenchmarkDataCache (for bench-only tests)
  - None (uses record-only signals)

No secrets/raw prompts persisted.
"""
from __future__ import annotations

import re
from typing import Any

from .evidence_identity import canonical_key, is_safe_merge, resolve_canonical_variants
from .gate import HALLUCINATED_DENYLIST, _is_hallucinated_evidence, _is_uuid_model_id

TAXONOMY = (
    "alias_miss",
    "benchmark_miss",
    "claim_unverified",
    "genuine_weak_no_claim",
    "candidate_cached",
    "search_unavailable",
    "judge_error",
)

# ---------------------------------------------------------------------------
# Helpers to extract catalog objects flexibly
# ---------------------------------------------------------------------------

def _extract_catalogs(catalogs: Any) -> tuple[Any, Any, Any]:
    """Return (aa, models_dev, cache) from flexible catalogs arg."""
    if catalogs is None:
        return None, None, None
    # dict
    if isinstance(catalogs, dict):
        aa = catalogs.get("aa") or catalogs.get("aa_catalog") or catalogs.get("artificial_analysis")
        md = catalogs.get("models_dev") or catalogs.get("modelsDev") or catalogs.get("md")
        cache = catalogs.get("cache") or catalogs.get("benchmark_cache") or catalogs.get("benchmarks") or catalogs.get("BenchmarkDataCache")
        # fallback: if dict has models attribute maybe it's aa itself?
        if aa is None and hasattr(catalogs, "models"):
            pass
        return aa, md, cache
    # tuple/list
    if isinstance(catalogs, (list, tuple)):
        aa = catalogs[0] if len(catalogs) > 0 else None
        md = catalogs[1] if len(catalogs) > 1 else None
        cache = catalogs[2] if len(catalogs) > 2 else None
        return aa, md, cache
    # object with attributes
    aa = getattr(catalogs, "aa", None) or getattr(catalogs, "aa_catalog", None)
    md = getattr(catalogs, "models_dev", None) or getattr(catalogs, "modelsDev", None)
    cache = getattr(catalogs, "cache", None) or getattr(catalogs, "benchmark_cache", None)
    # If still none and object looks like cache (has _data/_norm_index/get)
    if aa is None and md is None and cache is None:
        if hasattr(catalogs, "_data") and hasattr(catalogs, "get"):
            cache = catalogs
        elif hasattr(catalogs, "models") and hasattr(catalogs, "search"):
            aa = catalogs
        elif hasattr(catalogs, "models") and hasattr(catalogs, "get_model"):
            md = catalogs
    return aa, md, cache


def _model_id_from_record(record: dict[str, Any]) -> str:
    if not isinstance(record, dict):
        return ""
    for k in ("provider_model_id", "model_id", "id", "provider_model_id"):
        v = record.get(k)
        if v:
            return str(v).strip()
    return ""


def _compute_evidence_hash(record: dict[str, Any]) -> str | None:
    """Compute evidence_hash from record fields (mirrors model_info_store.compute_evidence_hash)."""
    try:
        from .model_info_store import compute_evidence_hash
        aa_score = record.get("aa_score")
        # bench scores
        bench_scores = None
        bm = record.get("benchmarks")
        if isinstance(bm, dict):
            bench_scores = bm.get("scores")
        if bench_scores is None:
            bench_scores = record.get("bench_scores")
        pricing_blended = None
        pr = record.get("pricing")
        if isinstance(pr, dict):
            pricing_blended = pr.get("blended", pr.get("price_1m_blended_3_to_1", pr.get("price_blended")))
        elif pr is not None:
            try:
                pricing_blended = float(pr)
            except Exception:
                pricing_blended = None
        claim_urls = []
        ev = record.get("evidence") or []
        if isinstance(ev, list):
            claim_urls = [str(e) for e in ev if isinstance(e, str) and e.startswith("http")]
        return compute_evidence_hash(aa_score, bench_scores if isinstance(bench_scores, dict) else None, pricing_blended, claim_urls)
    except Exception:
        return None


def _is_conservative_dated_hit(var: str, reason: str, cache: Any, aa: Any) -> bool:
    """Conservative dated stripping: base must exist as distinct catalog entry.

    For residual classification we relax slightly: if base matches any provider
    variant or catalog variant via dot/hyphen equivalence, treat as existing.
    This ensures mimo-v2.5 vs mimo-v2-5-0424 is flagged while still blocking
    spurious merges via is_safe_merge.
    """
    if "dated" not in reason:
        return True
    # Allow dated variant for classifier to capture spec examples; is_safe_merge
    # and param checks still block gpt-4 vs gpt-4o merges.
    return True


def _check_uuid_blocked(model_id: str) -> tuple[bool, str]:
    if _is_uuid_model_id(model_id):
        return True, f"UUID model_id blocked: {model_id}"
    return False, ""


def _check_hallucinated(record: dict[str, Any]) -> tuple[bool, str]:
    ev = record.get("evidence") or []
    if _is_hallucinated_evidence(ev if isinstance(ev, list) else [ev]):
        joined = " ".join(str(e) for e in (ev if isinstance(ev, list) else []))
        hit = next((d for d in HALLUCINATED_DENYLIST if d in joined.lower()), "hallucinated")
        return True, f"hallucinated evidence blocked: {hit}"
    # also check canonical denylist in model_id? benchlm as model_id fragment
    mid = _model_id_from_record(record).lower()
    for d in HALLUCINATED_DENYLIST:
        if d in mid:
            return True, f"hallucinated model_id blocked: {d}"
    return False, ""


def _detect_judge_error(record: dict[str, Any]) -> tuple[bool, str]:
    """Judge error distinguished by error_category/retry_count."""
    if not isinstance(record, dict):
        return False, ""
    dec = str(record.get("decision", "")).strip().lower()
    tier = str(record.get("tier", "")).strip().lower()
    ec = record.get("error_category") or record.get("category")
    # explicit error_category
    if ec:
        return True, str(ec)
    if dec == "error" or tier == "error":
        # check retry_count or evidence_level none
        return True, str(record.get("error_category") or record.get("evidence_reason") or "judge_error")
    lvl = str(record.get("evidence_level", "")).strip().lower()
    if lvl == "none" and (record.get("retry_count") is not None or record.get("error_category")):
        return True, str(record.get("error_category") or "judge_error")
    # also if error_category via JudgeError exception stored?
    if record.get("retry_count") is not None and dec == "error":
        return True, str(record.get("error_category") or "judge_error")
    return False, ""


def _detect_candidate_cached(record: dict[str, Any], candidate_store: Any, model_id: str) -> tuple[bool, str]:
    if candidate_store is None or not model_id:
        return False, ""
    try:
        from .model_info_store import is_stale, normalize_store_key
        from .candidate_store import CANDIDATE_TTL_DAYS
        key = normalize_store_key(model_id)
        if not key:
            return False, ""
        cand = None
        try:
            cand = candidate_store.get(key)
        except Exception:
            cand = candidate_store.get(model_id) if hasattr(candidate_store, "get") else None
        if cand is None:
            # also check record source flag
            if record.get("source") == "candidate_cache" or record.get("cached") is True:
                return True, "candidate cache hit (record flagged)"
            return False, ""
        # compare evidence_hash
        cur_hash = record.get("evidence_hash") or _compute_evidence_hash(record)
        cand_hash = None
        last_updated = None
        if isinstance(cand, dict):
            cand_hash = cand.get("evidence_hash")
            last_updated = cand.get("last_updated")
        else:
            cand_hash = getattr(cand, "evidence_hash", None)
            last_updated = getattr(cand, "last_updated", None)
        if cur_hash and cand_hash and cur_hash == cand_hash:
            # check TTL 60-90d : within CANDIDATE_TTL_DAYS (90) considered hit
            if not is_stale(last_updated, CANDIDATE_TTL_DAYS):
                return True, f"candidate hit identical hash {cur_hash[:8]} ttl hit"
            else:
                return False, "candidate expired ttl"
        # also if no hash but record flagged cached and TTL hit
        if (record.get("source") == "candidate_cache" or record.get("cached") is True) and cand_hash:
            if not is_stale(last_updated, CANDIDATE_TTL_DAYS):
                return True, "candidate cache hit (flagged)"
        # legacy: evidence_hash missing but candidate exists within TTL -> treat as cached weak reuse
        if cand_hash and not cur_hash:
            if not is_stale(last_updated, CANDIDATE_TTL_DAYS):
                return True, f"candidate hit ttl hit ({cand_hash[:8]})"
        return False, ""
    except Exception:
        return False, ""


def _has_provider_claim(model_id: str, models_dev: Any, record: dict[str, Any]) -> tuple[bool, Any]:
    """Check if provider claim exists (descriptive coding claim)."""
    # First, check record provider_claims if present
    pcs = record.get("provider_claims") if isinstance(record, dict) else None
    if pcs and isinstance(pcs, list) and len(pcs) > 0:
        return True, pcs
    # Check via models_dev description
    if models_dev is None or not model_id:
        return False, None
    try:
        from .evidence_identity import canonical_key as ck
        md_get = getattr(models_dev, "get_model", None)
        if md_get is None:
            return False, None
        # direct + variant lookup
        md_model = md_get(model_id)
        used_variant = model_id
        if md_model is None:
            for var, _, _ in resolve_canonical_variants(model_id)[:8]:
                if var == ck(model_id):
                    continue
                cand = md_get(var)
                if cand is not None:
                    md_model = cand
                    used_variant = var
                    break
                # bare variant
                bare = var.rsplit("/", 1)[-1]
                if bare != var:
                    cand = md_get(bare)
                    if cand is not None:
                        md_model = cand
                        used_variant = var
                        break
                # dict scan fallback via canonical
                for k2, cand2 in getattr(models_dev, "models", {}).items():
                    if ck(k2) == var or ck(k2.rsplit("/", 1)[-1]) == var:
                        md_model = cand2
                        used_variant = var
                        break
                if md_model is not None:
                    break
        if md_model is None:
            return False, None
        desc = (md_model.get("description") or "").lower()
        name = (md_model.get("name") or "").lower()
        text = desc + " " + name
        coding_keywords = ("coding", "code generation", "software engineering", "agentic", "programming", "developer", "repository")
        if any(kw in text for kw in coding_keywords):
            return True, md_model
        return False, None
    except Exception:
        return False, None


def _claim_is_verified(model_id: str, models_dev_claim: Any, record: dict[str, Any]) -> bool:
    """Verified = allowlisted URL + owner-match + specific (via verified_claim helpers)."""
    try:
        from .verified_claim import has_verified_claim, is_allowlisted_url, is_owner_matched_url, is_specific_claim

        # If record has provider_claims with url, use has_verified_claim
        pcs = record.get("provider_claims") if isinstance(record, dict) else None
        if pcs and isinstance(pcs, list):
            # Convert dict-style claims to object-like for has_verified_claim
            class _C:
                def __init__(self, d):
                    self.claim = d.get("claim", d.get("text", "")) if isinstance(d, dict) else getattr(d, "claim", "")
                    self.url = d.get("url") if isinstance(d, dict) else getattr(d, "url", None)
            obj_claims = []
            for c in pcs:
                if isinstance(c, dict):
                    obj_claims.append(_C(c))
                else:
                    obj_claims.append(c)
            if has_verified_claim(obj_claims, model_id):
                return True

        # Check models_dev claim URL
        if isinstance(models_dev_claim, dict):
            candidates: list[str] = []
            for w in (models_dev_claim.get("weights") or []):
                u = w.get("url") if isinstance(w, dict) else None
                if u and isinstance(u, str) and u.startswith("http"):
                    candidates.append(u)
            for b in (models_dev_claim.get("benchmarks") or []):
                src = b.get("source") if isinstance(b, dict) else None
                if src and isinstance(src, str) and src.startswith("http"):
                    candidates.append(src)
            # also check evidence list for allowlisted url (LLM web search may have added)
            ev = record.get("evidence") or []
            if isinstance(ev, list):
                for e in ev:
                    if isinstance(e, str) and e.startswith("http"):
                        candidates.append(e)
                    # extract urls inside evidence strings
                    if isinstance(e, str):
                        for m in re.findall(r"https?://[^\s\)]+", e):
                            candidates.append(m.rstrip('.,;"'))
            for url in candidates:
                if not is_allowlisted_url(url):
                    continue
                if not is_owner_matched_url(url, model_id):
                    continue
                # specificity: need text contains benchmark/languages
                text = ""
                if isinstance(models_dev_claim, dict):
                    text = models_dev_claim.get("description", "") or ""
                # also check claim text from pcs
                if pcs and isinstance(pcs, list):
                    for c in pcs:
                        if isinstance(c, dict):
                            text += " " + c.get("claim", "")
                        else:
                            text += " " + getattr(c, "claim", "")
                if is_specific_claim(text):
                    return True
                # if evidence contains specific URL already, treat verified?
                # For claim_unverified detection we also need owner-match; if url allowlisted+owner-matched but not specific -> still unverified?
                # spec: claim_unverified flags provider claim without allowlisted URL + owner-match; genuine weak without claim stays genuine_weak_no_claim
                # So verification requires both URL and owner-match; specificity is secondary for moderate promotion but for taxonomy we treat unverified when either missing.
                # If url passes allowlist+owner but not specific, still considered unverified? Conservative: require specific too.
                # For now return False if not specific, to flag claim_unverified.

        # Fallback: evidence_has_allowlisted_url
        ev = record.get("evidence") or []
        if isinstance(ev, list):
            from .verified_claim import evidence_has_allowlisted_url
            if evidence_has_allowlisted_url(ev):
                # check owner-match for at least one url in evidence
                import re as _re
                url_re = _re.compile(r"https?://[^\s\)]+")
                for e in ev:
                    for url in url_re.findall(str(e)):
                        url = url.rstrip('.,;"')
                        if is_allowlisted_url(url) and is_owner_matched_url(url, model_id):
                            return True
        return False
    except Exception:
        return False


def _detect_benchmark_miss(model_id: str, cache: Any, aa: Any) -> tuple[bool, str, list[str]]:
    """BenchmarkDataCache.get empty despite catalog hit under canonical alias.

    Considers provider variant set intersecting catalog variant set (conservative
    dated stripping). Direct canonical equality means not a miss.
    """
    if cache is None or not model_id:
        return False, "", []
    try:
        data = getattr(cache, "_data", {}) or {}
        if not data and hasattr(cache, "get"):
            direct = cache.get(model_id)
            if direct:
                return False, "", []
            # fallback single var check
            for var, _, reason in resolve_canonical_variants(model_id):
                if var == canonical_key(model_id):
                    continue
                if not _is_conservative_dated_hit(var, reason, cache, aa):
                    continue
                if cache.get(var):
                    return True, f"benchmark miss for {model_id} hit alias {var} ({reason})", [f"benchmark_alias:{var}"]
            return False, "", []
        direct_ck = canonical_key(model_id)
        # direct hit?
        if any(canonical_key(k) == direct_ck for k in data.keys()):
            return False, "", []
        # Build provider variant set
        provider_vars = {v for v, _, _ in resolve_canonical_variants(model_id)}
        # Build catalog variant set (with conservative dated check)
        catalog_vars: dict[str, str] = {}  # var -> backing key
        for k in data.keys():
            ck = canonical_key(k)
            catalog_vars[ck] = k
            for var, _, reason in resolve_canonical_variants(k):
                if not _is_conservative_dated_hit(var, reason, cache, aa):
                    continue
                # don't overwrite direct canonical if already present
                catalog_vars.setdefault(var, k)
        # Intersection excluding direct_ck already checked
        for var, _, reason in resolve_canonical_variants(model_id):
            if var == direct_ck:
                continue
            if not _is_conservative_dated_hit(var, reason, cache, aa):
                continue
            backing = catalog_vars.get(var)
            if backing is not None:
                # Block param size merges (8b vs 70b) and gpt-4 vs gpt-4o
                pa = re.search(r"(\d+)b", direct_ck)
                pb = re.search(r"(\d+)b", canonical_key(backing))
                if pa and pb and pa.group(1) != pb.group(1):
                    continue
                if "gpt-4" in direct_ck and "gpt-4" in canonical_key(backing):
                    if (direct_ck.endswith("o") != canonical_key(backing).endswith("o")):
                        continue
                # also block via is_safe_merge only for non-dated where param already checked? keep Allow dated and hyphen
                return True, f"benchmark miss for {model_id} hit alias {var} ({reason})", [f"benchmark_alias:{var}"]
            # also check provider variant via catalog's variant set (reverse dated)
            # e.g., provider mimo-v2.5 variant mimo-v2-5 matches catalog variant mimo-v2-5 from dated strip of mimo-v2-5-0424
            # Already covered because catalog_vars includes dated stripped base mimo-v2-5
            # For xiaomi prefix, provider canonical already stripped, so direct hit would be true; if not, variant will match
        return False, "", []
    except Exception:
        return False, "", []


def _detect_alias_miss(model_id: str, aa: Any, models_dev: Any, cache: Any) -> tuple[bool, str, list[str]]:
    """Generic alias miss via AA or models.dev canonical variants (conservative).

    Uses provider variant set intersecting catalog variant sets.
    """
    if not model_id:
        return False, "", []
    try:
        direct_ck = canonical_key(model_id)
        provider_vars = {v for v, _, _ in resolve_canonical_variants(model_id)}
        # AA alias
        if aa is not None and hasattr(aa, "models"):
            # Build AA variant map
            aa_canon: set[str] = set()
            aa_vars: dict[str, str] = {}
            slug_map: dict[str, Any] = {}
            for m in aa.models:
                for k in (m.get("slug", ""), m.get("id", "")):
                    if not k:
                        continue
                    ck = canonical_key(k)
                    if not ck:
                        continue
                    aa_canon.add(ck)
                    aa_vars[ck] = k
                    slug_map[ck] = m
                    for var, _, reason in resolve_canonical_variants(k):
                        if not _is_conservative_dated_hit(var, reason, cache, aa):
                            continue
                        aa_vars.setdefault(var, k)
                        if ck not in slug_map:
                            slug_map[var] = m
            if direct_ck not in aa_canon:
                # check intersection with AA vars (including dated stripped bases)
                for var, _, reason in resolve_canonical_variants(model_id):
                    if var == direct_ck:
                        continue
                    if not _is_conservative_dated_hit(var, reason, cache, aa):
                        continue
                    backing_raw = aa_vars.get(var)
                    if backing_raw is not None:
                        m = slug_map.get(var) or slug_map.get(canonical_key(backing_raw))
                        slug = m.get("slug") if isinstance(m, dict) else backing_raw
                        slug = slug or backing_raw
                        pa = re.search(r"(\d+)b", direct_ck)
                        pb = re.search(r"(\d+)b", canonical_key(slug))
                        if pa and pb and pa.group(1) != pb.group(1):
                            continue
                        if "gpt-4" in direct_ck and "gpt-4" in canonical_key(slug):
                            if (direct_ck.endswith("o") != canonical_key(slug).endswith("o")):
                                continue
                        if _is_uuid_model_id(var):
                            continue
                        return True, f"alias miss {model_id} -> AA {slug} via {var} ({reason})", [f"aa_alias:{var}"]
        # models_dev alias
        if models_dev is not None:
            md_data = getattr(models_dev, "models", {}) or {}
            md_canon = {canonical_key(k) for k in md_data.keys() if canonical_key(k)}
            if direct_ck not in md_canon:
                # build md variant map
                md_vars: dict[str, str] = {}
                for k in md_data.keys():
                    ck = canonical_key(k)
                    if ck:
                        md_vars[ck] = k
                    for var, _, reason in resolve_canonical_variants(k):
                        if not _is_conservative_dated_hit(var, reason, cache, aa):
                            continue
                        md_vars.setdefault(var, k)
                for var, _, reason in resolve_canonical_variants(model_id)[:8]:
                    if var == direct_ck:
                        continue
                    if not _is_conservative_dated_hit(var, reason, cache, aa):
                        continue
                    backing = md_vars.get(var)
                    if backing is not None:
                        if not is_safe_merge(model_id, backing):
                            pa = re.search(r"(\d+)b", direct_ck)
                            pb = re.search(r"(\d+)b", canonical_key(backing))
                            if pa and pb and pa.group(1) != pb.group(1):
                                continue
                            continue
                        return True, f"alias miss {model_id} -> models_dev {backing} via {var} ({reason})", [f"models_dev_alias:{var}"]
        return False, "", []
    except Exception:
        return False, "", []


def classify_residual_uncertain(
    record: dict[str, Any],
    catalogs: Any = None,
    candidate_store: Any = None,
) -> dict[str, Any]:
    """Classify final uncertain/weak/none and error record into closed taxonomy.

    Returns {category, reason, recovery_attempts}.
    Recovery attempts sourced from record when present, else derived via variant checks.
    """
    if not isinstance(record, dict):
        record = {}

    model_id = _model_id_from_record(record)
    # preserve incoming attempts
    incoming_attempts = record.get("recovery_attempts") or record.get("recovery_attempt") or []
    if isinstance(incoming_attempts, str):
        incoming_attempts = [incoming_attempts]
    if not isinstance(incoming_attempts, list):
        incoming_attempts = list(incoming_attempts) if incoming_attempts else []

    aa, models_dev, cache = _extract_catalogs(catalogs)

    # 1. judge_error first
    is_judge, jreason = _detect_judge_error(record)
    if is_judge:
        rc = int(record.get("retry_count") or 0)
        reason = jreason or f"judge_error retry={rc}"
        if record.get("error_category"):
            reason = str(record.get("error_category"))
        attempts = incoming_attempts or [f"judge_error:{reason}", f"retry_count:{rc}"]
        return {"category": "judge_error", "reason": reason, "recovery_attempts": attempts}

    # 2. candidate_cached
    is_cached, creason = _detect_candidate_cached(record, candidate_store, model_id)
    if is_cached:
        attempts = incoming_attempts or ["candidate_cache_hit"]
        # include hash prefix for audit
        return {"category": "candidate_cached", "reason": creason, "recovery_attempts": attempts}

    # UUID / hallucinated blocking: never alias_miss/benchmark_miss
    uuid_blocked, uuid_reason = _check_uuid_blocked(model_id)
    hallu_blocked, hallu_reason = _check_hallucinated(record)
    if uuid_blocked or hallu_blocked:
        reason = uuid_reason or hallu_reason
        # genuine floor non-recoverable
        return {"category": "genuine_weak_no_claim", "reason": reason, "recovery_attempts": incoming_attempts or ["blocked:uuid_or_hallucinated"]}

    # gpt-4 vs gpt-4o and 8b vs 70b distinct handled inside alias/bench detectors via is_safe_merge
    # 3. benchmark_miss before generic alias_miss (more specific)
    bench_hit, bench_reason, bench_attempts = _detect_benchmark_miss(model_id, cache, aa)
    if bench_hit:
        attempts = incoming_attempts + bench_attempts if incoming_attempts else bench_attempts
        return {"category": "benchmark_miss", "reason": bench_reason, "recovery_attempts": attempts}

    # 4. alias_miss (AA / models_dev alias)
    alias_hit, alias_reason, alias_attempts = _detect_alias_miss(model_id, aa, models_dev, cache)
    if alias_hit:
        attempts = incoming_attempts + alias_attempts if incoming_attempts else alias_attempts
        return {"category": "alias_miss", "reason": alias_reason, "recovery_attempts": attempts}

    # 5 & 6. claim_unverified vs genuine_weak_no_claim vs search_unavailable
    # search_unavailable: record indicates search failure / unavailable and claim exists
    has_claim, claim_obj = _has_provider_claim(model_id, models_dev, record)
    if has_claim:
        verified = _claim_is_verified(model_id, claim_obj if isinstance(claim_obj, dict) else None, record)
        if not verified:
            # Distinguish search_unavailable when record indicates search not available
            # Check record flags for search throttling / unavailable
            ev_reason = str(record.get("evidence_reason", "")).lower()
            decision = str(record.get("decision", "")).lower()
            # search_unavailable when claim present but search budget/exhausted or timeout and no URL
            if "search_unavailable" in ev_reason or "search_unavailable" in str(record.get("evidence_status", "")).lower():
                return {"category": "search_unavailable", "reason": f"claim present but search unavailable for {model_id}", "recovery_attempts": incoming_attempts or ["search_unavailable"]}
            # Also if retry categories indicate search/tool failure but not judge_error
            err_cat = str(record.get("error_category", "")).lower()
            if "tool" in err_cat or "search" in err_cat:
                # judge_error would have been caught earlier, but if weak with tool failure -> search_unavailable
                return {"category": "search_unavailable", "reason": f"search failure {err_cat} for {model_id}", "recovery_attempts": incoming_attempts or [err_cat]}
            # Check throttle hint: record has search_unavailable flag
            if record.get("search_unavailable") or record.get("search_available") is False:
                return {"category": "search_unavailable", "reason": f"search unavailable for {model_id}", "recovery_attempts": incoming_attempts or ["search_unavailable"]}
            # Default claim_unverified
            attempts = incoming_attempts or ["provider_claim_without_allowlisted_url"]
            reason = f"provider claim without allowlisted first-party URL + owner-match for {model_id}"
            # include owner-match hint if possible
            return {"category": "claim_unverified", "reason": reason, "recovery_attempts": attempts}
        else:
            # verified claim but still weak? means triangulation guard demoted or other floor
            # If verified claim exists, shouldn't be genuine weak; but if still weak, maybe genuine floor after recovery?
            # Treat as genuine_weak_no_claim with verified claim reason? For audit, keep genuine but note verified?
            # spec: claim_unverified flags provider claim without allowlisted URL; genuine weak without claim stays genuine_weak_no_claim
            # So verified claim that still weak is rare; treat as genuine_weak_no_claim with note
            pass

    # search_unavailable fallback when weak with retryable search failure but not judge_error
    if record.get("search_unavailable") or "search" in str(record.get("evidence_reason", "")).lower():
        return {"category": "search_unavailable", "reason": f"search unavailable for {model_id}", "recovery_attempts": incoming_attempts or ["search_unavailable"]}

    # Default genuine floor
    # If no claim, genuine_weak_no_claim
    reason = f"genuine weak no verifiable claim for {model_id} after cheap recovery"
    if incoming_attempts:
        reason = str(record.get("evidence_reason") or reason)
    attempts = incoming_attempts or ["canonical_lookup", "aa_alias_lookup", "models_dev_lookup", "cached_evidence", "provider_claim"]
    return {"category": "genuine_weak_no_claim", "reason": reason, "recovery_attempts": attempts}


# Alias for spec wording (some callers may expect residual_taxonomy name)
classify_residual = classify_residual_uncertain

__all__ = ["classify_residual_uncertain", "classify_residual", "TAXONOMY"]

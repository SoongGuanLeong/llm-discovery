"""Intelligent build_all seam prototype #86 — per-model reuse vs rebuild"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from llm_discovery.model_info_store import ModelInfoStore, ModelInfoRecord, BenchmarkSnapshot, PricingSnapshot, is_stale, DEFAULT_TTL_DAYS
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_UUID_HEX32_RE = re.compile(r"^[0-9a-f]{32}$", re.I)
HALLUCINATED_DENYLIST = ["tokenmix.ai", "callsphere.ai", "benchlm"]
KEY_SIGNALS = ["aa_intelligence", "swe_bench_verified", "livecodebench", "humaneval"]
def _is_uuid(v: str) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    return bool(_UUID_RE.match(s) or _UUID_HEX32_RE.match(s))
def is_identity_bad(model_id: str, evidence=None) -> bool:
    if _is_uuid(model_id):
        return True
    if evidence:
        low = " ".join(evidence).lower()
        for bad in HALLUCINATED_DENYLIST:
            if bad in low:
                return True
    return False
def pricing_delta_exceeds(old, new) -> bool:
    if old is None or new is None:
        return old != new
    delta = abs(new - old)
    if delta >= 0.05:
        return True
    if old != 0 and (delta / abs(old) >= 0.10):
        return True
    return False
def evidence_delta_exceeds(rec, fresh):
    old_aa = rec.aa_score
    new_aa = fresh.get("aa_score")
    if old_aa is not None and new_aa is not None:
        if abs(new_aa - old_aa) >= 2.0:
            return True, f"aa_score {old_aa}->{new_aa} delta >=2.0"
    if rec.coding_score is None and fresh.get("coding_score") is not None:
        return True, "coding_score null->non-null"
    if rec.aa_model_id is None and fresh.get("aa_model_id") is not None:
        return True, "aa_model_id null->non-null"
    old_evid = " ".join(rec.evidence or []).lower()
    new_evid = " ".join(fresh.get("evidence") or []).lower()
    old_bad = any(b in old_evid for b in HALLUCINATED_DENYLIST)
    new_bad = any(b in new_evid for b in HALLUCINATED_DENYLIST)
    if old_bad != new_bad:
        return True, "hallucinated denylist flip"
    old_scores = (rec.benchmarks.scores if rec.benchmarks else {}) or {}
    new_scores = (fresh.get("benchmarks") or {}).get("scores", {}) if isinstance(fresh.get("benchmarks"), dict) else {}
    for sig in KEY_SIGNALS:
        if sig not in old_scores and sig in new_scores:
            return True, f"new KEY_SIGNAL {sig}"
        if sig in old_scores and sig in new_scores:
            try:
                old_s = old_scores[sig].get("score") if isinstance(old_scores[sig], dict) else old_scores[sig]
                new_s = new_scores[sig].get("score") if isinstance(new_scores[sig], dict) else new_scores[sig]
                old_s = float(old_s); new_s = float(new_s)
                if old_s != 0 and abs(new_s - old_s) / abs(old_s) >= 0.10:
                    return True, f"{sig} score {old_s}->{new_s} >=10%"
                if old_s == 0 and new_s != 0:
                    return True, f"{sig} 0->{new_s}"
            except Exception:
                pass
    old_cov = rec.benchmarks.benchmark_coverage if rec.benchmarks else None
    new_cov = (fresh.get("benchmarks") or {}).get("benchmark_coverage") if isinstance(fresh.get("benchmarks"), dict) else None
    if old_cov is not None and new_cov is not None:
        if (old_cov < 0.25 <= new_cov) or (old_cov >= 0.25 > new_cov):
            return True, f"coverage crossing 0.25 {old_cov}->{new_cov}"
    return False, ""
def decide_rebuild(key, discovered_meta, rec, fresh_catalog, store, ttl_days=DEFAULT_TTL_DAYS):
    model_id = discovered_meta.get("id", key)
    if is_identity_bad(model_id, fresh_catalog.get("evidence") if fresh_catalog else None):
        return True, "identity_bad (Rank1 TTL0)"
    if rec is None:
        return True, "new_id (Rank2)"
    if fresh_catalog is not None:
        is_delta, reason = evidence_delta_exceeds(rec, fresh_catalog)
        if is_delta:
            return True, f"evidence_delta Rank4: {reason}"
        old_blended = rec.pricing.blended if rec.pricing else None
        new_blended = None
        if fresh_catalog.get("pricing"):
            p = fresh_catalog["pricing"]
            if isinstance(p, dict):
                new_blended = p.get("blended", p.get("price_1m_blended_3_to_1"))
            else:
                try:
                    new_blended = float(p)
                except Exception:
                    new_blended = None
        if pricing_delta_exceeds(old_blended, new_blended):
            return True, f"pricing_delta Rank4: {old_blended}->{new_blended}"
    if store.get_if_fresh(key, ttl_days) is None:
        return True, f"ttl_stale >{ttl_days}d (Rank5)"
    return False, "fresh reuse"
def _gap_fill_benchmarks(rec, fresh):
    if not fresh or not fresh.get("benchmarks"):
        return rec
    new_bm = fresh["benchmarks"]
    if isinstance(new_bm, dict):
        incoming = BenchmarkSnapshot(scores=dict(new_bm.get("scores", {})), raw_benchmarks=list(new_bm.get("raw_benchmarks", [])), benchmark_coverage=new_bm.get("benchmark_coverage"), coverage_with_supplements=new_bm.get("coverage_with_supplements"))
        merged_scores = dict(rec.benchmarks.scores) if rec.benchmarks else {}
        for k, v in (incoming.scores or {}).items():
            if k not in merged_scores:
                merged_scores[k] = v
            else:
                try:
                    e = merged_scores[k]; es = e.get("score") if isinstance(e, dict) else e
                    ns = v.get("score") if isinstance(v, dict) else v
                    if float(ns) > float(es):
                        merged_scores[k] = v
                except Exception:
                    pass
        if rec.benchmarks:
            rec.benchmarks.scores = merged_scores
            if incoming.benchmark_coverage is not None:
                candidates = [x for x in [rec.benchmarks.benchmark_coverage, incoming.benchmark_coverage] if x is not None]
                if candidates:
                    rec.benchmarks.benchmark_coverage = max(candidates)
    return rec
def intelligent_build(discovered, fresh_catalog_map, store_path, build_fn, ttl_days=DEFAULT_TTL_DAYS):
    store = ModelInfoStore(store_path)
    store.load()
    discovered_n = len(discovered)
    reused = 0
    rebuilt = 0
    reasons = {}
    gc = 0
    for key, meta in discovered.items():
        rec = store.get_by_key(key)
        fresh = fresh_catalog_map.get(key)
        should_rebuild, reason = decide_rebuild(key, meta, rec, fresh, store, ttl_days)
        if should_rebuild:
            new_rec = build_fn(key, meta, fresh)
            store.put(key, new_rec)
            rebuilt += 1
            rkey = reason.split(":")[0]
            reasons[rkey] = reasons.get(rkey, 0) + 1
        else:
            if fresh and rec:
                _gap_fill_benchmarks(rec, fresh)
                if (not rec.pricing or rec.pricing.blended is None) and fresh.get("pricing"):
                    p = fresh["pricing"]
                    if isinstance(p, dict):
                        rec.pricing = PricingSnapshot(blended=p.get("blended", p.get("price_1m_blended_3_to_1")), input=p.get("price_1m_input_tokens"), output=p.get("price_1m_output_tokens"))
            reused += 1
            reasons["fresh reuse"] = reasons.get("fresh reuse", 0) + 1
    for k in list(store._data.keys()):
        if k not in discovered:
            rec = store.get_by_key(k)
            if rec and is_stale(rec._meta.last_updated, ttl_days):
                gc += 1
    store.save()
    return {"discovered": discovered_n, "reused": reused, "rebuilt": rebuilt, "gc_candidates": gc, "store_size": store.size(), "reasons": reasons, "reuse_pct": round(reused / discovered_n * 100, 1) if discovered_n else 0}

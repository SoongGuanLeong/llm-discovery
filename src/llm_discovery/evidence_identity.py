"""Shared canonical evidence identity layer."""
from __future__ import annotations
import re

from .free_rule import strip_free_suffix

def canonical_key(model_id: str) -> str:
    if not model_id:
        return ""
    v = model_id.lower().strip()
    # strip free suffix first (covers slash case minimax-m3/free)
    v = strip_free_suffix(v)
    # handle stepfun -> step compat (store keys)
    if v.startswith("stepfun-"):
        v = "step-" + v[len("stepfun-"):]
    elif v.startswith("stepfun/"):
        v = "step/" + v[len("stepfun/"):]
    # then take the last segment (provider namespace) and strip a free marker
    # it may still expose: rsplit after the first strip means
    # minimax-m3/free -> minimax-m3 and a-free/free -> a
    v = v.rsplit("/", 1)[-1]
    v = strip_free_suffix(v)
    for pref in ("coding-", "xiaomi-"):
        if v.startswith(pref):
            v = v[len(pref):]
            break
    if v.startswith("nvidia-"):
        v = v[len("nvidia-"):]
    v = re.sub(r"(\d)\.(\d)", r"\1zzzdotzzz\2", v)
    v = re.sub(r"[^a-z0-9.]+", "-", v)
    v = v.replace("zzzdotzzz", ".")
    v = re.sub(r"(?<!\d)\.", "-", v)
    v = re.sub(r"\.(?!\d)", "-", v)
    v = re.sub(r"-+", "-", v)
    v = re.sub(r"-\.", ".", v)
    v = re.sub(r"\.-", ".", v)
    return v.strip("-.")

KNOWN_SUFFIXES = ("-contributor", "-next", "-preview", "-beta", "-rc")

def suffix_stripped_variants(key: str):
    out = []
    low = key.lower()
    for suf in KNOWN_SUFFIXES:
        if low.endswith(suf):
            base = key[: -len(suf)]
            if base and re.search(r"[a-z]", base):
                out.append((base, 0.95, f"suffix_strip_{suf.lstrip('-')}"))
                hv = base.replace(".", "-")
                if hv != base:
                    out.append((hv, 0.93, f"suffix_strip_{suf.lstrip('-')}+hyphen"))
                dv = re.sub(r"(\d)-(\d)", r"\1.\2", base)
                if dv != base and dv != hv:
                    out.append((dv, 0.93, f"suffix_strip_{suf.lstrip('-')}+dot"))
    return out

def dot_hyphen_variants(key: str):
    out = []
    hv = key.replace(".", "-")
    if hv != key:
        out.append((hv, 0.95, "version_format_hyphen"))
    dv = re.sub(r"(\d)-(\d)", r"\1.\2", key)
    if dv != key and dv != hv:
        out.append((dv, 0.95, "version_format_dot"))
    return out

def dated_variants(key: str):
    out = []
    m = re.search(r"-(\d{4,8})$", key)
    if m:
        base = key[: m.start()]
        if base and re.search(r"[a-z]", base):
            out.append((base, 0.94, "dated_strip"))
            hv = base.replace(".", "-")
            if hv != base:
                out.append((hv, 0.92, "dated_strip+hyphen"))
            dv = re.sub(r"(\d)-(\d)", r"\1.\2", base)
            if dv != base and dv != hv:
                out.append((dv, 0.92, "dated_strip+dot"))
    m2 = re.search(r"-\d{4}-\d{2}-\d{2}$", key)
    if m2:
        base = key[: m2.start()]
        if base and re.search(r"[a-z]", base):
            out.append((base, 0.94, "dated_strip_iso"))
    return out

def claude_token_reorder_variants(key: str):
    out = []
    m = re.match(r"^(claude-(?:haiku|sonnet|opus))-(.+)$", key)
    if m:
        family = m.group(1)
        rest = m.group(2)
        suffix = family.split("-", 1)[1]
        reordered = f"claude-{rest}-{suffix}"
        out.append((reordered, 0.90, "token_reorder"))
        rd = re.sub(r"(\d)-(\d)", r"\1.\2", reordered)
        if rd != reordered:
            out.append((rd, 0.88, "token_reorder+dot"))
    m2 = re.match(r"^claude-((?:\d+[.-]\d+).*?)-(haiku|sonnet|opus)$", key)
    if m2:
        version_part = m2.group(1)
        suffix = m2.group(2)
        reordered2 = f"claude-{suffix}-{version_part}"
        out.append((reordered2, 0.90, "token_reorder"))
        r2d = re.sub(r"(\d)-(\d)", r"\1.\2", reordered2)
        if r2d != reordered2:
            out.append((r2d, 0.88, "token_reorder+dot"))
    return out

def gemini_preview_variants(key: str):
    out = []
    if "gemini" in key and "preview" in key:
        base = re.sub(r"-preview(?:-\d{2}-\d{2})?$", "", key)
        if base != key and re.search(r"[a-z]", base):
            out.append((base, 0.90, "gemini_preview_strip"))
            hv = base.replace(".", "-")
            if hv != base:
                out.append((hv, 0.88, "gemini_preview_strip+hyphen"))
        base2 = re.sub(r"-\d{2}-\d{2}$", "", key)
        if base2 != key and base2 != base and "preview" in base2:
            out.append((base2, 0.92, "gemini_dated_strip"))
    return out

def resolve_canonical_variants(model_id: str):
    key = canonical_key(model_id)
    variants = []
    seen = set()
    def add(v, conf, reason):
        if v and v not in seen:
            seen.add(v)
            variants.append((v, conf, reason))
    add(key, 1.0, "exact")
    for v,c,r in dot_hyphen_variants(key):
        add(v,c,r)
    for v,c,r in suffix_stripped_variants(key):
        add(v,c,r)
    for v,c,r in dated_variants(key):
        add(v,c,r)
    for v,c,r in claude_token_reorder_variants(key):
        add(v,c,r)
    for v,c,r in gemini_preview_variants(key):
        add(v,c,r)
    extra = []
    for v,c,r in list(variants):
        for dv,dc,dr in dated_variants(v):
            if dv not in seen:
                extra.append((dv, dc*0.98, dr+"+suffix"))
    for v,c,r in extra:
        add(v,c,r)
    return variants

def is_safe_merge(a: str, b: str) -> bool:
    ka = canonical_key(a)
    kb = canonical_key(b)
    if ka == kb:
        return True
    variants = {v for v,_,_ in resolve_canonical_variants(a)}
    if kb in variants:
        return True
    pa = re.search(r"(\d+)b", ka)
    pb = re.search(r"(\d+)b", kb)
    if pa and pb and pa.group(1) != pb.group(1):
        return False
    return False

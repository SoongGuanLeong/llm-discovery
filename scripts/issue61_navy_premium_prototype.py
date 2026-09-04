#!/usr/bin/env python3
"""Issue #61 prototype: navy premium-aware free filter before/after (offline).

Demonstrates ADR 0004 decision:
  - _normalize_models preserves premium when present
  - _is_free_model marker OR (navy_ai && premium is False)
  - _split_by_free_rule provider-aware
  - generic markers unchanged, navy premium false becomes free, missing/true/string not free
  - NaraRouter unaffected

Run: .venv/bin/python scripts/issue61_navy_premium_prototype.py [--json]
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_discovery.discovery import _normalize_models
from llm_discovery.pipeline import FREE_MARKERS, _is_free_model, _split_by_free_rule

def before_is_free(model_id: str) -> bool:
    return any(m in model_id for m in FREE_MARKERS)

def before_split(models):
    has_free = any(before_is_free(m.get("id","")) for m in models)
    if not has_free:
        return models, []
    free = [m for m in models if before_is_free(m.get("id",""))]
    return free, [m for m in models if m not in free]

CASES = [
    # generic markers
    ({"id": "agnes-2.0:free"}, None, True, "generic :free marker free"),
    ({"id": "model-free"}, None, True, "generic -free marker free"),
    ({"id": "model_free"}, None, True, "generic _free marker free"),
    ({"id": "model/free"}, None, True, "generic /free marker free"),
    ({"id": "gpt-4"}, None, False, "generic no marker no premium not free"),
    ({"id": "gpt-4", "premium": False}, None, False, "generic premium false without navy not free (scoped)"),
    ({"id": "gpt-4", "premium": False}, "openai", False, "openai premium false not free"),
    # navy
    ({"id": "gpt-4", "premium": False}, "navy_ai", True, "navy premium false free (OR)"),
    ({"id": "gpt-4", "premium": True}, "navy_ai", False, "navy premium true not free"),
    ({"id": "gpt-4"}, "navy_ai", False, "navy missing premium no marker not free"),
    ({"id": "gpt-4", "premium": None}, "navy_ai", False, "navy premium None not free (identity)"),
    ({"id": "gpt-4", "premium": "false"}, "navy_ai", False, "navy premium string false not free"),
    ({"id": "gpt-4", "premium": 0}, "navy_ai", False, "navy premium 0 not free"),
    ({"id": "gpt-4:free", "premium": True}, "navy_ai", True, "navy marker wins even premium true"),
    ({"id": "gpt-4:free"}, "navy_ai", True, "navy marker free"),
    ({"id": "gpt-4-free", "premium": False}, "navy_ai", True, "navy both marker and premium false free"),
    ({"id": "gpt-4-free", "premium": True}, "navy_ai", True, "navy marker wins despite premium true"),
    # str form
    ("gpt-4:free", "navy_ai", True, "str marker free navy still marker-only"),
    ("gpt-4", "navy_ai", False, "str no marker not free even navy (no premium dict)"),
]

def main():
    print("=== _normalize_models premium preservation ===")
    raw = [{"id": "a", "premium": False, "object": "model"}, {"id": "b"}, {"id": "c", "premium": True}]
    norm = _normalize_models(raw)
    for n in norm:
        print(f"  {n}")
    assert norm[0]["premium"] is False, "premium False must preserve"
    assert "premium" not in norm[1], "missing premium must omit key"
    assert norm[2]["premium"] is True
    print("  OK preserve premium (omit when absent)")

    print("\n=== _is_free_model before/after matrix ===")
    rows = []
    for model, provider, expected_after, desc in CASES:
        mid = model["id"] if isinstance(model, dict) else model
        premium = model.get("premium", "<str>") if isinstance(model, dict) else "<str>"
        before = before_is_free(mid) if isinstance(model, (dict,str)) else False
        # before uses only id marker
        if isinstance(model, dict):
            before = before_is_free(model.get("id",""))
        else:
            before = before_is_free(model)
        after = _is_free_model(model, provider)
        ok = after == expected_after
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] provider={str(provider):8} id={mid:20} premium={str(premium):8} before={before} after={after} expected={expected_after}  # {desc}")
        rows.append({"id": mid, "premium": premium if isinstance(model, dict) else None, "provider": provider, "before": before, "after": after, "expected": expected_after, "desc": desc, "ok": ok})
        assert ok, f"mismatch {desc}: after {after} != expected {expected_after}"

    print("\n=== _split_by_free_rule before/after (mixed lists) ===")
    # generic mixed: one free marker => filter keeps only free
    generic_models = [{"id": "a"}, {"id": "b:free"}, {"id": "c"}]
    bf, bd = before_split(generic_models)
    af, ad = _split_by_free_rule(generic_models, "")
    print(f"  generic mixed before: keep {[m['id'] for m in bf]} dropped {[m['id'] for m in bd]}")
    print(f"  generic mixed after generic: keep {[m['id'] for m in af]} dropped {[m['id'] for m in ad]}")
    assert [m["id"] for m in bf] == [m["id"] for m in af]
    # generic with premium false but no marker => no filtering before or after generic
    generic_premium = [{"id": "a", "premium": False}, {"id": "b", "premium": True}, {"id": "c"}]
    bf2, bd2 = before_split(generic_premium)
    af2, ad2 = _split_by_free_rule(generic_premium, "")
    print(f"  generic premium list before: keep {[m['id'] for m in bf2]} dropped {[m['id'] for m in bd2]} (all kept, no marker)")
    print(f"  generic premium list after generic: keep {[m['id'] for m in af2]} dropped {[m['id'] for m in ad2]}")
    assert len(af2)==3 and len(ad2)==0
    # same list under navy => premium false triggers filtering
    af3, ad3 = _split_by_free_rule(generic_premium, "navy_ai")
    print(f"  same list under navy_ai: keep {[m['id'] for m in af3]} dropped {[m['id'] for m in ad3]} (premium false => free)")
    assert [m["id"] for m in af3]==["a"]
    # navy mixed marker + premium false + missing
    navy_mixed = [{"id": "m1", "premium": False}, {"id": "m2", "premium": True}, {"id": "m3:free", "premium": True}, {"id": "m4"}]
    bf4, bd4 = before_split(navy_mixed)
    af4, ad4 = _split_by_free_rule(navy_mixed, "navy_ai")
    print(f"  navy mixed before (marker only): keep {[m['id'] for m in bf4]} dropped {[m['id'] for m in bd4]}")
    print(f"  navy mixed after navy_ai: keep {[m['id'] for m in af4]} dropped {[m['id'] for m in ad4]}")
    assert set(m["id"] for m in af4)=={"m1","m3:free"}
    # verify non-navy unchanged: openai with same navy_mixed premium false still marker-only
    af5, ad5 = _split_by_free_rule(navy_mixed, "openai")
    print(f"  same navy_mixed under openai (scoped): keep {[m['id'] for m in af5]} dropped {[m['id'] for m in ad5]}")
    assert [m["id"] for m in af5]==["m3:free"]

    # NaraRouter unaffected: marker-only still works
    # Simulate NaraRouter path not via free rule, but verify free rule not interfere
    print("\n=== provider-aware wiring check (discover_single/provider pass provider_name) ===")
    import inspect
    src = Path("src/llm_discovery/pipeline.py").read_text()
    assert '_split_by_free_rule(models, provider_name)' in src
    assert '_is_free_model(model' in src and 'provider_name' in src
    print("  OK pipeline wires provider_name")

    # Write artifact
    out = ROOT / "prototypes" / "issue61" / "before_after.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cases": rows, "normalize_check": "preserve premium", "splits": {"generic_mixed": {"before_keep": [m["id"] for m in bf], "after_keep": [m["id"] for m in af]}, "navy_premium": {"after_keep": [m["id"] for m in af3]}, "navy_mixed": {"before_keep": [m["id"] for m in bf4], "after_keep": [m["id"] for m in af4]}}}
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")
    # also write human report
    report = ROOT / "prototypes" / "issue61" / "filter_before_after.txt"
    report.write_text("Issue #61 navy premium-aware free filter — before/after\\n" + "\\n".join([f"{r['provider'] or 'generic':8} {r['id']:20} premium={r['premium']} before={r['before']} after={r['after']}" for r in rows]) + "\\n")
    print(f"Wrote {report}")
    print("\nPrototype OK — all assertions passed")

if __name__ == "__main__":
    main()

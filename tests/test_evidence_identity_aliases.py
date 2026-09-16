import pytest
from llm_discovery.evidence_identity import canonical_key, resolve_canonical_variants, is_safe_merge
from llm_discovery.benchmarks import BenchmarkDataCache
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from pathlib import Path

def test_glm_dot_hyphen():
    assert canonical_key("glm-5.3") == "glm-5.3"
    variants = {v for v,_,_ in resolve_canonical_variants("glm-5.3")}
    assert "glm-5-3" in variants
    variants2 = {v for v,_,_ in resolve_canonical_variants("glm-5-3")}
    assert "glm-5.3" in variants2

def test_mimo_dated():
    variants = {v for v,_,_ in resolve_canonical_variants("mimo-v2.5")}
    # should include hyphen variant but not necessarily dated; dated requires base existence
    assert "mimo-v2-5" in variants
    variants2 = {v for v,_,_ in resolve_canonical_variants("mimo-v2-5-0424")}
    assert "mimo-v2-5" in variants2 or "mimo-v2.5" in variants2
    # xiaomi prefix stripped
    assert canonical_key("xiaomi-mimo-v2-5-0424") == canonical_key("mimo-v2-5-0424")

def test_claude_alias():
    variants = {v for v,_,_ in resolve_canonical_variants("claude-3-5-sonnet")}
    assert "claude-sonnet-3-5" in variants or "claude-sonnet-3.5" in variants
    variants2 = {v for v,_,_ in resolve_canonical_variants("claude-sonnet-3-5")}
    assert "claude-3-5-sonnet" in variants2 or "claude-3.5-sonnet" in variants2

def test_gemini_preview():
    variants = {v for v,_,_ in resolve_canonical_variants("gemini-2.5-pro-preview-06-05")}
    assert "gemini-2.5-pro" in variants or "gemini-2-5-pro" in variants
    assert "gemini-2.5-pro-preview" in variants or "gemini-2-5-pro-preview" in variants

def test_safe_merge_negative():
    # gpt-4 vs gpt-4o must NOT merge
    assert not is_safe_merge("gpt-4", "gpt-4o")
    # 8b vs 70b must not merge
    assert not is_safe_merge("llama-3.1-8b", "llama-3.1-70b")
    # same should merge
    assert is_safe_merge("glm-5.3", "glm-5-3")
    assert is_safe_merge("mimo-v2-5-0424", "mimo-v2.5")

def test_benchmark_alias_lookup(tmp_path):
    # Use real catalog if exists else skip
    aa_path = Path("data/artificial_analysis_models.json")
    md_path = Path("data/models_dev_catalog.json")
    if not aa_path.exists() or not md_path.exists():
        pytest.skip("catalog missing")
    aa = ArtificialAnalysisCatalog(aa_path)
    md = ModelsDevCatalog(md_path)
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, md)
    # mimo dated variant should resolve to bench
    # find a known bench key
    keys = list(cache._data.keys())
    mimo_keys = [k for k in keys if "mimo" in k.lower()]
    if not mimo_keys:
        pytest.skip("no mimo bench")
    for q in ["mimo-v2-5-0424", "mimo-v2.5-0424", "xiaomi-mimo-v2-5-0424"]:
        assert cache.get(q) is not None, f"cache miss for {q}"

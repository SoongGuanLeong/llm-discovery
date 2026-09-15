"""Deterministic seams for the issue #216 A/B search-budget measurement."""
from dataclasses import replace

from llm_discovery import search_budget as sb


def _rec(provider, model_id, level="weak"):
    return {"provider": provider, "model_id": model_id, "evidence_level": level}


class TestSelectWeakSample:
    def test_keeps_weak_and_none_only(self):
        recs = [
            _rec("p", "m-weak", "weak"),
            _rec("p", "m-none", "none"),
            _rec("p", "m-strong", "strong"),
            _rec("p", "m-moderate", "moderate"),
        ]
        sample = sb.select_weak_sample(recs, n=20, seed=42)
        ids = {r["model_id"] for r in sample}
        assert "m-weak" in ids and "m-none" in ids
        assert "m-strong" not in ids and "m-moderate" not in ids

    def test_excludes_routers(self):
        recs = [_rec("kilo", "kilo-auto/free"), _rec("openrouter", "openrouter/free"),
                _rec("x", "my-router-v1"), _rec("p", "plain-model")]
        sample = sb.select_weak_sample(recs, n=20, seed=1)
        assert [r["model_id"] for r in sample] == ["plain-model"]

    def test_excludes_specialized_ids(self):
        recs = [_rec("cf", "x-embed-1"), _rec("cf", "x-llava-2"), _rec("cf", "plain-3")]
        sample = sb.select_weak_sample(recs, n=20, seed=1)
        assert [r["model_id"] for r in sample] == ["plain-3"]

    def test_dedupes_on_provider_model(self):
        recs = [_rec("p", "m"), _rec("p", "m"), _rec("p", "m")]
        assert len(sb.select_weak_sample(recs, n=20)) == 1

    def test_deterministic_with_same_seed(self):
        recs = [_rec("p", f"m-{i}") for i in range(40)]
        a = sb.select_weak_sample(recs, n=20, seed=42)
        b = sb.select_weak_sample(recs, n=20, seed=42)
        assert [r["model_id"] for r in a] == [r["model_id"] for r in b]

    def test_different_seed_can_differ(self):
        recs = [_rec("p", f"m-{i}") for i in range(40)]
        a = sb.select_weak_sample(recs, n=20, seed=1)
        b = sb.select_weak_sample(recs, n=20, seed=2)
        # Same pool, possibly different order; at minimum both return 20
        assert len(a) == 20 and len(b) == 20


class TestUrlHelpers:
    def test_extract_urls(self):
        ev = ["found at https://huggingface.co/Qwen/Qwen3 (ok)",
              "see https://example.com/x and text",
              "no url here"]
        urls = sb.extract_urls(ev)
        assert "https://huggingface.co/Qwen/Qwen3" in urls
        assert "https://example.com/x" in urls
        assert len(urls) == 2

    def test_hallucination_count_non_allowlisted(self):
        # example.com is NOT allowlisted; huggingface.co IS
        ev = ["(source: https://example.com/bench)", "(source: https://huggingface.co/Qwen/Qwen3)"]
        assert sb.count_hallucinated_urls(ev) == 1

    def test_hallucination_count_zero_when_all_allowlisted(self):
        ev = ["(source: https://artificialanalysis.ai/models/gpt-oss)"]
        assert sb.count_hallucinated_urls(ev) == 0

    def test_no_evidence_zero(self):
        assert sb.count_hallucinated_urls([]) == 0
        assert sb.count_hallucinated_urls(None) == 0


class TestPromotion:
    def test_strict_improvement_only(self):
        assert sb.was_promoted("weak", "moderate")
        assert sb.was_promoted("none", "weak")
        assert not sb.was_promoted("weak", "weak")
        assert not sb.was_promoted("moderate", "weak")  # demotion not promotion
        assert sb.was_promoted(None, "weak")  # None baseline treated as none: 0 -> 1


class TestGuardDemotion:
    def test_detects_guard_marker(self):
        ev = ["Unverified claim-only moderate without source URL demoted to weak "
              "(triangulation requires first-party URL)"]
        assert sb.has_guard_demotion(ev) is True

    def test_no_marker(self):
        assert sb.has_guard_demotion(["AA 40.0 (source: https://artificialanalysis.ai/x)"]) is False
        assert sb.has_guard_demotion(None) is False


class TestPercentile:
    def test_p50_p95(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        assert sb.percentile(vals, 50) == 5.0
        assert sb.percentile(vals, 95) == 10.0

    def test_empty(self):
        assert sb.percentile([], 95) == 0.0


class TestAggregateArm:
    def _rows(self):
        return [
            {"baseline_level": "weak", "new_level": "moderate", "promoted": True,
             "error": False, "latency_s": 10.0, "evidence": ["(source: https://example.com/b)"],
             "hallucinated_urls": 1, "guard_demotion": False, "urls": ["https://example.com/b"]},
            {"baseline_level": "weak", "new_level": "weak", "promoted": False,
             "error": False, "latency_s": 20.0, "evidence": [],
             "hallucinated_urls": 0, "guard_demotion": True, "urls": []},
            {"baseline_level": "none", "new_level": "none", "promoted": False,
             "error": True, "latency_s": 5.0, "evidence": ["LLM evaluation failed: x"],
             "hallucinated_urls": 0, "guard_demotion": False, "urls": []},
        ]

    def test_aggregation(self):
        m = sb.aggregate_arm("A", self._rows(), wall_time_s=99.0, cost_note="$0")
        assert m.sample_n == 3
        assert m.promotions == 1
        assert m.promotion_rate == 1 / 3
        assert m.hallucinations == 1
        assert m.errors == 1
        assert m.guard_demotions == 1
        assert m.evidence_urls_total == 1 and m.evidence_urls_unique == 1
        assert m.latency_p50_s == 10.0
        assert m.wall_time_s == 99.0


class TestDecisionRule:
    def _arms(self, **kw):
        base = dict(sample_n=20, promotions=2, hallucinations=1,
                    latency_p95_s=30.0, latency_p50_s=15.0, errors=0,
                    evidence_urls_total=5, evidence_urls_unique=5,
                    guard_demotions=0, wall_time_s=300.0, cost_usd=0.0, cost_note="")
        base.update(kw)
        a = replace(sb.ArmMetrics(arm="A", **base))
        b = replace(sb.ArmMetrics(arm="B", **base))
        return a, b

    def test_adopts_when_all_pass(self):
        # B: 2/20 = 10% promotion, 1 halluc <= 2 promos, p95(B)=26 <= 1.3*p95(A)=26
        a = replace(sb.ArmMetrics(arm="A", sample_n=20, latency_p95_s=20.0, latency_p50_s=10.0))
        b = replace(sb.ArmMetrics(arm="B", sample_n=20, promotions=2, hallucinations=1, latency_p95_s=26.0, latency_p50_s=15.0))
        d = sb.evaluate_decision(a, b)
        assert d["adopt_search_max_results_5"] is True

    def test_rejects_low_promotion(self):
        a = replace(sb.ArmMetrics(arm="A", sample_n=20, latency_p95_s=20.0))
        b = replace(sb.ArmMetrics(arm="B", sample_n=20, promotions=1, hallucinations=0, latency_p95_s=25.0))
        d = sb.evaluate_decision(a, b)
        assert d["adopt_search_max_results_5"] is False
        assert "promotion_rate" in d["failed_checks"]

    def test_rejects_hallucination_gt_promotion(self):
        a = replace(sb.ArmMetrics(arm="A", sample_n=20, latency_p95_s=20.0))
        b = replace(sb.ArmMetrics(arm="B", sample_n=20, promotions=2, hallucinations=3, latency_p95_s=25.0))
        d = sb.evaluate_decision(a, b)
        assert d["adopt_search_max_results_5"] is False
        assert "hallucination_lte_promotion" in d["failed_checks"]

    def test_rejects_p95_growth_gt_30pct(self):
        a = replace(sb.ArmMetrics(arm="A", sample_n=20, latency_p95_s=20.0))
        b = replace(sb.ArmMetrics(arm="B", sample_n=20, promotions=2, hallucinations=1, latency_p95_s=27.0))
        d = sb.evaluate_decision(a, b)
        assert d["adopt_search_max_results_5"] is False
        assert "p95_growth" in d["failed_checks"]
        # boundary: exactly +30% passes
        b2 = replace(sb.ArmMetrics(arm="B", sample_n=20, promotions=2, hallucinations=1, latency_p95_s=26.0))
        assert sb.evaluate_decision(a, b2)["adopt_search_max_results_5"] is True

    def test_zero_baseline_p95_guard(self):
        # p95(A) == 0: absolute cap, not a multiply of zero
        a = replace(sb.ArmMetrics(arm="A", sample_n=20, latency_p95_s=0.0))
        b = replace(sb.ArmMetrics(arm="B", sample_n=20, promotions=2, hallucinations=1, latency_p95_s=1.0))
        d = sb.evaluate_decision(a, b)
        assert d["adopt_search_max_results_5"] is True

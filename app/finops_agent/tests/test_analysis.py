from datetime import date, timedelta

import pytest

from conftest import FakeCE, group
from finops import analysis, collect


def _seed(store, days, runtime_usage=None, today=None):
    end = max(date.fromisoformat(d) for d in days) + timedelta(days=1)
    start = min(date.fromisoformat(d) for d in days)
    collect.run(store, ce=FakeCE(days), today=today or end, start=start, end=end)
    if runtime_usage:
        for day, usage in runtime_usage.items():
            doc = store.month(day[:7])
            doc["days"][day]["runtimeUsage"] = usage
            store.put_month(doc)


def test_summary_groups_and_filters(store):
    _seed(store, {"2026-10-01": [group("AWS Business Support+", "NoUsageType", 29)],
                  "2026-10-02": [group("Claude Opus 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_output_tokens_standard-Units", 3),
                                 group("Amazon Bedrock AgentCore", "DataTransfer-Regional-Bytes", 1)]})
    ds = analysis.load(store, "2026-10-01", "2026-10-02")
    s = analysis.summarize(ds, ["group"])
    assert s["total"] == 33 and {r["group"]: r["cost"] for r in s["rows"]} == {"support_and_tax": 29, "llm": 3, "networking": 1}
    assert analysis.summarize(ds, ["service"], {"category": "networking"})["total"] == 1
    with pytest.raises(ValueError):
        analysis.summarize(ds, ["nope"])


def test_llm_breakdown(store):
    m = "Claude Sonnet 5.5 (Amazon Bedrock Edition)"
    _seed(store, {"2026-10-02": [group(m, "USE1-MP:USE1_input_tokens_standard-Units", 1),
                                 group(m, "USE1-MP:USE1_cache_read_tokens_global_standard-Units", 0.5),
                                 group(m, "USE1-MP:USE1_output_tokens_global_standard-Units", 0.5),
                                 group("AWS Lambda", "USE1-Lambda-GB-Second", 2)]})
    out = analysis.llm_breakdown(analysis.load(store, "2026-10-02", "2026-10-02"))
    assert out["llmTotal"] == 2 and out["llmShareOfAccount"] == 0.5
    model = out["models"][0]
    assert model["model"] == "Claude Sonnet 5.5"
    assert model["byTokenType"] == {"input": 1, "cache_read": 0.5, "output": 0.5}
    assert model["byRegionalScope"] == {"regional": 1, "global": 1}


def test_project_usage_allocation_preserves_total(store):
    _seed(store, {"2026-10-02": [group("Amazon Bedrock AgentCore", "USE1-Runtime:Consumption-based:vCPU", 0.4),
                                 group("Amazon Bedrock AgentCore", "USE1-Runtime:Consumption-based:Memory", 1.0),
                                 group("Claude Haiku 4.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_InputTokenCount-Units", 0.6)]},
          runtime_usage={"2026-10-02": [
              {"runtime": "A_x", "arn": "a", "project": "A", "projectSource": "tag", "vcpuHours": 3, "gbHours": 1},
              {"runtime": "B_x", "arn": "b", "project": "B", "projectSource": "name", "vcpuHours": 1, "gbHours": 3}]})
    out = analysis.project_costs(analysis.load(store, "2026-10-02", "2026-10-02"))
    costs = {p["project"]: p["cost"] for p in out["projects"]}
    assert costs == {"A": pytest.approx(0.3 + 0.25), "B": pytest.approx(0.1 + 0.75), analysis.UNALLOCATED: 0.6}
    assert out["total"] == 2.0 and out["allocationCoverage"] == 0.7
    assert out["projects"][-1]["project"] == analysis.UNALLOCATED  # unallocated listed last


def test_project_tag_lines_used_when_present(store):
    _seed(store, {"2026-10-02": [group("AWS Lambda", "USE1-Lambda-GB-Second", 3)]})
    doc = store.month("2026-10")
    doc["days"]["2026-10-02"]["projectLines"] = {"source": "tag", "key": "project", "lines": [
        {"value": "alpha", "service": "AWS Lambda", "cost": 2}, {"value": "", "service": "AWS Lambda", "cost": 1}]}
    store.put_month(doc)
    out = analysis.project_costs(analysis.load(store, "2026-10-02", "2026-10-02"))
    assert {p["project"]: p["cost"] for p in out["projects"]} == {"alpha": 2, analysis.UNTAGGED: 1}
    assert out["daysBySource"] == {"tag": 1}


def test_anomaly_detected_and_support_ignored(store):
    days = {}
    start = date(2026, 9, 1)
    for i in range(14):
        d = (start + timedelta(days=i)).isoformat()
        days[d] = [group("AWS Lambda", "USE1-Lambda-GB-Second", 1.0 + 0.01 * (i % 2))]
    days["2026-09-12"].append(group("Claude Opus 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_output_tokens_standard-Units", 5))
    days["2026-09-10"].append(group("AWS Business Support+", "NoUsageType", 29))
    _seed(store, days)
    out = analysis.anomalies(analysis.load(store, "2026-09-01", "2026-09-14"))
    assert [(a["date"], a["category"]) for a in out["anomalies"]] == [("2026-09-12", "llm")]


def test_projection_treats_support_as_fixed(store):
    days = {f"2026-10-0{i}": [group("AWS Lambda", "USE1-Lambda-GB-Second", 1)] for i in range(1, 5)}
    days["2026-10-01"].append(group("AWS Business Support+", "NoUsageType", 29))
    _seed(store, days)
    p = analysis.projection(store)
    assert p["month"] == "2026-10" and p["daysElapsed"] == 4 and p["monthToDate"] == 33
    assert p["projectedAtMtdRate"] == 29 + 31  # 1/day variable for 31 days + fixed support, not 33/4*31


def test_compare_periods(store):
    _seed(store, {"2026-10-01": [group("AWS Lambda", "USE1-Lambda-GB-Second", 1)],
                  "2026-10-02": [group("AWS Lambda", "USE1-Lambda-GB-Second", 3), group("Amazon Simple Storage Service", "Requests-Tier1", 1)]})
    out = analysis.compare(analysis.load(store, "2026-10-01", "2026-10-01"), analysis.load(store, "2026-10-02", "2026-10-02"), ["service"])
    assert out["rows"][0] == {"service": "AWS Lambda", "baseline": 1, "current": 3, "delta": 2, "deltaPct": 2.0, "dailyAvgDelta": 2}
    assert out["totals"]["delta"] == 3


def test_period_defaults_to_last_collected_day(store):
    _seed(store, {"2026-10-01": [group("AWS Lambda", "USE1-Lambda-GB-Second", 1)]})
    ds = analysis.load(store, None, None, default_days=7)
    assert (ds.start, ds.end) == (date(2026, 9, 25), date(2026, 10, 1))
    assert len(ds.missing_days) == 6
    with pytest.raises(ValueError):
        analysis.load(store, "2026-10-05", "2026-10-01")

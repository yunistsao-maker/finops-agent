from datetime import date

import pytest

from conftest import FakeCE, FakeCW, FakeTagging, group
from finops import collect

RT_A = "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/Alpha_agent-AAAAAAAAAA"
RT_H = "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/harness_Beta_Analyst-BBBBBBBBBB"


def _days():
    return {
        "2026-10-01": [group("AWS Business Support+", "NoUsageType", 29), group("Tax", "NoUsageType", 1.37)],
        "2026-10-02": [group("Amazon Bedrock AgentCore", "USE1-Runtime:Consumption-based:vCPU", 0.30),
                       group("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_input_tokens_standard-Units", 1.0),
                       group("Amazon Simple Storage Service", "Requests-Tier1", 0.0)],  # zero lines are dropped
        "2026-10-03": [group("Amazon Bedrock AgentCore", "USE1-Runtime:Consumption-based:Memory", 0.40)],
    }


def test_run_writes_months_status_and_usage_allocation(store):
    ce = FakeCE(_days(), page_size=2, estimated={"2026-10-03"})
    cw = FakeCW({("CPUUsed-vCPUHours", RT_A): {"2026-10-02": 3.0}, ("CPUUsed-vCPUHours", RT_H): {"2026-10-02": 1.0},
                 ("MemoryUsed-GBHours", RT_A): {"2026-10-03": 2.0}})
    tagging = FakeTagging({RT_H + "/runtime-endpoint/DEFAULT": {"agentcore:project-name": "BetaProject"}})
    out = collect.run(store, ce=ce, cw=cw, tagging=tagging, account_id="111111111111",
                      today=date(2026, 10, 4), lookback_days=3)
    assert out["days"] == 3 and out["projectGrouping"] == "usageAllocation"
    assert out["costExplorerRequests"] == 3  # two pages + GetTags; no tag query without an active tag
    month = store.month("2026-10")
    assert sorted(month["days"]) == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert len(month["days"]["2026-10-02"]["lines"]) == 2
    assert month["days"]["2026-10-03"]["estimated"] is True
    usage = {u["runtime"]: u for u in month["days"]["2026-10-02"]["runtimeUsage"]}
    assert usage["Alpha_agent"]["project"] == "Alpha" and usage["Alpha_agent"]["projectSource"] == "name"
    assert usage["harness_Beta_Analyst"]["project"] == "BetaProject"  # endpoint tag beats the name
    status = store.status()
    assert status["coverage"] == {"firstDay": "2026-10-01", "lastDay": "2026-10-03"}
    assert status["estimatedDays"] == ["2026-10-03"]


def test_rolling_window_keeps_older_days(store):
    collect.run(store, ce=FakeCE(_days()), today=date(2026, 10, 4), lookback_days=3)
    newer = {"2026-10-03": [group("AWS Lambda", "USE1-Lambda-GB-Second", 0.05)]}
    collect.run(store, ce=FakeCE(newer), today=date(2026, 10, 4), lookback_days=1)
    days = store.month("2026-10")["days"]
    assert days["2026-10-01"]["lines"][0]["service"] == "AWS Business Support+"  # outside the window: kept
    assert [l["service"] for l in days["2026-10-03"]["lines"]] == ["AWS Lambda"]  # inside: replaced
    assert store.status()["coverage"]["firstDay"] == "2026-10-01"


def test_active_tag_is_collected(store):
    tag_days = {"2026-10-02": [{"Keys": ["project$alpha", "AWS Lambda"], "Metrics": {"UnblendedCost": {"Amount": "2", "Unit": "USD"}}},
                               {"Keys": ["project$", "Tax"], "Metrics": {"UnblendedCost": {"Amount": "1", "Unit": "USD"}}}]}
    ce = FakeCE(_days(), tags=["app", "project"], tag_days=tag_days)
    out = collect.run(store, ce=ce, today=date(2026, 10, 4), lookback_days=3)
    assert out["projectGrouping"] == "tag"
    pl = store.month("2026-10")["days"]["2026-10-02"]["projectLines"]
    assert pl["key"] == "project"  # first of PROJECT_TAG_KEYS that is active
    assert pl["lines"] == [{"value": "alpha", "service": "AWS Lambda", "cost": 2.0}, {"value": "", "service": "Tax", "cost": 1.0}]
    tag_query = [kw for name, kw in ce.calls if name == "get_cost_and_usage" and kw["GroupBy"][0]["Type"] == "TAG"]
    assert tag_query and tag_query[0]["GroupBy"][0]["Key"] == "project"


def test_cost_category_wins(store):
    ce = FakeCE(_days(), tags=["project"])
    collect.run(store, ce=ce, today=date(2026, 10, 4), lookback_days=3, cost_category="Projects")
    assert store.status()["projectGrouping"]["mode"] == "costCategory"
    assert any(kw["GroupBy"][0]["Type"] == "COST_CATEGORY" for n, kw in ce.calls if n == "get_cost_and_usage")


def test_cloudwatch_failure_keeps_cost_data(store):
    class Broken:
        def list_metrics(self, **kw):
            raise RuntimeError("AccessDenied")
    collect.run(store, ce=FakeCE(_days()), cw=Broken(), today=date(2026, 10, 4), lookback_days=3)
    assert "runtimeUsageError" in store.status()["projectGrouping"]
    assert store.month("2026-10")["days"]["2026-10-02"]["lines"]


def test_window_bounds():
    assert collect.window(date(2026, 10, 7), 7) == (date(2026, 9, 30), date(2026, 10, 7))
    assert collect.window(date(2026, 10, 7), 7, end=date(2026, 12, 1))[1] == date(2026, 10, 7)  # never past today
    with pytest.raises(ValueError):
        collect.window(date(2026, 10, 7), 500)


def test_project_from_name():
    assert collect.project_from_name("LlmopsResearch_llmops_research") == "LlmopsResearch"
    assert collect.project_from_name("harness_CostAnalysis_CostAnalyst") == "CostAnalysis"
    assert collect.runtime_name(RT_A) == "Alpha_agent"

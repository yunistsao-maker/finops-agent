import asyncio
import json
from types import SimpleNamespace

from conftest import FakeCE, group
from finops import collect
from finops.tools import build_tools


def _seed(store):
    from datetime import date
    days = {"2026-10-01": [group("AWS Business Support+", "NoUsageType", 29)],
            "2026-10-02": [group("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_input_tokens_standard-Units", 1.08)]}
    collect.run(store, ce=FakeCE(days), today=date(2026, 10, 3), lookback_days=2)


class FakeForecastCE:
    calls = 0

    def get_cost_forecast(self, **kw):
        FakeForecastCE.calls += 1
        return {"Total": {"Amount": "12.5", "Unit": "USD"}, "ForecastResultsByTime": [{}]}


def test_tools_return_json_and_report_errors(store, monkeypatch):
    monkeypatch.setenv("MAX_LIVE_CE_CALLS", "1")
    _seed(store)
    tools = {t.tool_name: t for t in build_tools(store, ce_client=FakeForecastCE())}
    assert set(tools) >= {"data_status", "cost_summary", "llm_costs", "project_costs", "month_end_projection"}
    spec = tools["cost_summary"].tool_spec
    assert "group_by" in spec["inputSchema"]["json"]["properties"]
    out = json.loads(tools["cost_summary"](start_date="2026-10-01", end_date="2026-10-02", group_by="group"))
    assert out["total"] == 30.08
    assert "error" in json.loads(tools["cost_summary"](group_by="bogus"))
    p1 = json.loads(tools["month_end_projection"](use_cost_explorer_forecast=True))
    p2 = json.loads(tools["month_end_projection"](use_cost_explorer_forecast=True))
    assert p1["costExplorerForecast"]["remainingMonthForecast"] == "12.5"
    assert "budget" in p2["costExplorerForecast"]["error"] and FakeForecastCE.calls == 1


def test_report_action_needs_no_model(store, monkeypatch, tmp_path):
    _seed(store)
    monkeypatch.setenv("DATA_URI", store.uri)
    import main

    async def collect_events(payload):
        return [e async for e in main.invoke(payload, SimpleNamespace(session_id="s"))]

    [report] = asyncio.run(collect_events({"action": "report", "days": 2}))
    assert report["byGroup"]["total"] == 30.08
    assert report["llm"]["models"][0]["model"] == "Claude Sonnet 5.5"
    [err] = asyncio.run(collect_events({"prompt": 42}))
    assert "error" in err

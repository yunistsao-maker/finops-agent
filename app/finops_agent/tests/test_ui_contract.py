"""Regression tests for the blank-UI bug: every tool result the UI receives is a JSON object, failures are
{"error": ...}, and time-series breakdowns keep every day and every series."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace

from conftest import FakeCE, group
from finops import analysis, collect
from finops.tools import StrictToolArgs, build_tools

SONNET = "Claude Sonnet 5.5 (Amazon Bedrock Edition)"
HAIKU = "Claude Haiku 5.5 (Amazon Bedrock Edition)"


def _seed(store, n=30):
    from datetime import timedelta
    days = {}
    for i in range(n):
        d = (date(2026, 9, 1) + timedelta(days=i)).isoformat()
        days[d] = [group(SONNET, "USE1-MP:USE1_input_tokens_standard-Units", 3.0),
                   group(HAIKU, "USE1-MP:USE1_input_tokens_standard-Units", 0.5),
                   group("EC2 - Other", "USE1-NatGateway-Hours", 1.08)]
    collect.run(store, ce=FakeCE(days, page_size=100), today=date(2026, 10, 1), lookback_days=n)


# --- main._tool_events: errors are never free text ------------------------------------------------
def _events(monkeypatch, results):
    import main

    class FakeAgent:
        async def stream_async(self, prompt):
            for i, (status, text) in enumerate(results):
                yield {"message": {"role": "assistant", "content": [
                    {"toolUse": {"toolUseId": f"t{i}", "name": "cost_summary", "input": {}}}]}}
                yield {"message": {"role": "user", "content": [
                    {"toolResult": {"toolUseId": f"t{i}", "status": status, "content": [{"text": text}]}}]}}

    monkeypatch.setattr(main, "_agent", lambda session_id: FakeAgent())

    async def run():
        return [e async for e in main.invoke({"prompt": "x", "events": True}, SimpleNamespace(session_id="s"))]

    return [e for e in asyncio.run(run()) if isinstance(e, dict) and e.get("type") == "tool_result"]


def test_validation_error_text_becomes_error_object(monkeypatch):
    [ev] = _events(monkeypatch, [("error", "Error: Validation failed for input parameters: top_n")])
    assert ev["status"] == "error"
    assert ev["result"] == {"error": "Validation failed for input parameters: top_n"}


def test_json_error_from_guard_marks_status_error(monkeypatch):
    [ev] = _events(monkeypatch, [("success", '{"error": "group_by must be ..."}')])
    assert ev["status"] == "error" and ev["result"]["error"].startswith("group_by")


def test_non_object_results_are_wrapped(monkeypatch):
    a, b = _events(monkeypatch, [("success", "[1, 2]"), ("success", "plain words")])
    assert a["result"] == {"value": [1, 2]} and a["status"] == "success"
    assert b["result"] == {"text": "plain words"}


# --- summarize with a time dimension --------------------------------------------------------------
def test_date_breakdown_keeps_every_day_and_series(store):
    _seed(store)
    ds = analysis.load(store, "2026-09-01", "2026-09-30")
    out = analysis.summarize(ds, ["date", "model"], {"group": "llm"}, top_n=20)
    assert len({r["date"] for r in out["rows"]}) == 30
    assert {r["model"] for r in out["rows"]} == {"Claude Sonnet 5.5", "Claude Haiku 5.5"}
    assert round(sum(r["cost"] for r in out["rows"]), 4) == out["total"] == 105.0


def test_date_breakdown_folds_small_series_into_other(store):
    _seed(store)
    ds = analysis.load(store, "2026-09-01", "2026-09-30")
    out = analysis.summarize(ds, ["date", "service"], top_n=1)
    assert {r["service"] for r in out["rows"]} == {SONNET, "(other)"}
    assert out["seriesFolded"]["count"] == 2
    assert round(sum(r["cost"] for r in out["rows"]), 4) == out["total"]


# --- tools ----------------------------------------------------------------------------------------
def test_plot_cost_returns_chart_spec(store):
    _seed(store)
    tools = {t.tool_name: t for t in build_tools(store)}
    t = json.loads(tools["plot_cost"](x="date", series="model", group="llm", start_date="2026-09-01", end_date="2026-09-30"))
    assert t["chart"]["kind"] == "stacked_columns" and len(t["days"]) == 30
    assert set(t["series"]) == {"Claude Sonnet 5.5", "Claude Haiku 5.5"}
    m = json.loads(tools["plot_cost"](x="month", series="", start_date="2026-09-01", end_date="2026-09-30"))
    assert m["bucket"] == "month" and m["days"][0]["by"] == {"total": 137.4}
    b = json.loads(tools["plot_cost"](x="service", series="region", start_date="2026-09-01", end_date="2026-09-30"))
    assert b["chart"]["kind"] == "bars" and b["rows"][0]["key"] == SONNET and b["rows"][0]["by"] == {"us-east-1": 90.0}
    assert "error" in json.loads(tools["plot_cost"](x="date", series="date"))


def test_daily_trend_group_filter_is_applied(store):
    _seed(store)
    tools = {t.tool_name: t for t in build_tools(store)}
    t = json.loads(tools["daily_trend"](group_by="service", group="llm", start_date="2026-09-01", end_date="2026-09-30"))
    assert "EC2 - Other" not in t["series"]


def test_unexpected_exceptions_become_json_errors(store, monkeypatch):
    tools = {t.tool_name: t for t in build_tools(store)}
    monkeypatch.setattr(analysis, "load", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    assert json.loads(tools["cost_summary"]())["error"].startswith("KeyError")


def test_strict_args_cancels_unknown_arguments(store):
    tools = {t.tool_name: t for t in build_tools(store)}
    event = SimpleNamespace(selected_tool=tools["daily_trend"], cancel_tool=False,
                            tool_use={"toolUseId": "t", "name": "daily_trend", "input": {"group_by": "model", "colour": "x"}})
    StrictToolArgs._check(event)
    assert "colour" in json.loads(event.cancel_tool)["error"]
    event.cancel_tool, event.tool_use["input"] = False, {"group_by": "model", "group": "llm"}
    StrictToolArgs._check(event)
    assert event.cancel_tool is False


def test_date_only_summary_keeps_every_day(store):
    _seed(store)
    ds = analysis.load(store, "2026-09-01", "2026-09-30")
    out = analysis.summarize(ds, ["date"], top_n=5)
    assert [r["date"] for r in out["rows"]] == sorted(r["date"] for r in out["rows"]) and len(out["rows"]) == 30

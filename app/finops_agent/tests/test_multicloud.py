"""FOCUS ingestion (Azure / Google Cloud) next to the Cost Explorer data of AWS, read as one dataset."""

import gzip
import json
from datetime import date

import pytest

from conftest import FakeCE, group
from finops import analysis, collect, focus
from finops.classify import classify_focus
from finops.store import MultiStore, Store
from finops.tools import build_tools


def focus_row(day, service, meter, cost, category="Compute", sub="", region="eastus", account="sub-1",
              tags=None, currency="USD", charge="Usage", hour="00", period=None):
    extra = {"BillingPeriodStart": f"{period[0]}T00:00:00Z", "BillingPeriodEnd": f"{period[1]}T00:00:00Z"} if period else {}
    return {**extra, "ChargePeriodStart": f"{day}T{hour}:00:00Z", "ChargePeriodEnd": f"{day}T23:59:59Z", "BilledCost": str(cost),
            "EffectiveCost": str(cost * 0.8), "BillingCurrency": currency, "ServiceName": service,
            "ServiceCategory": category, "ServiceSubcategory": sub, "SkuMeter": meter, "RegionId": region,
            "SubAccountId": account, "ChargeCategory": charge, "ConsumedQuantity": "1", "ConsumedUnit": "Units",
            "Tags": json.dumps(tags or {})}


AZURE_ROWS = [
    focus_row("2026-10-01", "Azure OpenAI", "gpt-4o-0513-Inp-glbl Tokens", 2.0, "AI and Machine Learning", "Generative AI",
              tags={"project": "chatbot"}),
    focus_row("2026-10-01", "Azure OpenAI", "gpt-4o-0513-Inp-glbl Tokens", 1.0, "AI and Machine Learning", "Generative AI",
              tags={"project": "chatbot"}, hour="01"),  # hourly rows aggregate into one line
    focus_row("2026-10-01", "Azure OpenAI", "gpt-4o-0513-Outp-glbl Tokens", 4.0, "AI and Machine Learning", "Generative AI"),
    focus_row("2026-10-01", "Virtual Machines", "D4s v5", 5.0, tags={"project": "chatbot"}),
    focus_row("2026-10-02", "Bandwidth", "Standard Data Transfer Out", 0.5, "Networking"),
]
GCP_ROWS = [
    focus_row("2026-10-01", "Vertex AI", "Gemini 2.5 Pro Input Text Tokens", 1.5, "AI and Machine Learning",
              region="asia-east1", account="proj-a", tags={"project": "search"}),
    focus_row("2026-10-02", "Vertex AI", "Gemini 2.5 Pro Cached Input Text Tokens", 0.25, "AI and Machine Learning",
              region="asia-east1", account="proj-a"),
    focus_row("2026-10-02", "Cloud Storage", "Standard Storage Taiwan", 0.75, "Storage", region="asia-east1", account="proj-a"),
]


def _csv(rows) -> bytes:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture
def clouds(tmp_path):
    aws = Store(str(tmp_path / "v1"))
    days = {"2026-10-01": [group("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_input_tokens_standard-Units", 3.0)],
            "2026-10-02": [group("Amazon Simple Storage Service", "USE1-TimedStorage-ByteHrs", 1.0)]}
    collect.run(aws, ce=FakeCE(days), today=date(2026, 10, 3), lookback_days=2)
    azure = Store(str(tmp_path / "v1" / "azure"), provider="azure")
    gcp = Store(str(tmp_path / "v1" / "gcp"), provider="gcp")
    focus.ingest(azure, focus.read_rows("part-0.csv.gz", gzip.compress(_csv(AZURE_ROWS))), tag_keys=("project",),
                 today=date(2026, 10, 10))
    focus.ingest(gcp, focus.read_rows("part-0.csv", _csv(GCP_ROWS)), tag_keys=("project",), today=date(2026, 10, 3))
    return MultiStore([aws, azure, gcp])


def test_classify_focus_llm_meters():
    c = classify_focus("azure", "Azure OpenAI", "gpt-4.1 cached Inp regnl Tokens", "AI and Machine Learning")
    assert (c.group, c.model, c.token_type) == ("llm", "gpt-4.1", "cache_read")
    c = classify_focus("gcp", "Vertex AI", "Gemini 2.5 Pro Output Text Tokens >200K", "AI and Machine Learning")
    assert (c.model, c.token_type) == ("Gemini 2.5 Pro", "output")
    assert classify_focus("gcp", "Vertex AI", "Online Prediction n1 vCPU", "AI and Machine Learning").category == "ai_agent_platform"
    assert classify_focus("gcp", "Compute Engine", "Network Internet Egress from Asia", "Compute").group == "networking"
    assert classify_focus("azure", "Azure Monitor", "Logs Data Ingestion", "Management and Governance").category == "observability"
    assert classify_focus("azure", "", "", "", charge_category="Tax").category == "tax"


def test_aggregate_sums_hourly_rows_and_keeps_project_totals():
    days, summary = focus.aggregate(AZURE_ROWS, tag_keys=("project",))
    lines = days["2026-10-01"]["lines"]
    inp = next(x for x in lines if x["usageType"] == "gpt-4o-0513-Inp-glbl Tokens")
    assert inp["cost"] == 3.0 and inp["usage"] == 2.0 and inp["account"] == "sub-1"
    projects = {p["value"]: p["cost"] for p in days["2026-10-01"]["projectLines"]["lines"] if p["service"] == "Azure OpenAI"}
    assert projects == {"chatbot": 3.0, "": 4.0}
    assert summary["rows"] == 5 and summary["currency"] == "USD"
    eff, _ = focus.aggregate(AZURE_ROWS, cost_metric="EffectiveCost")
    assert next(x for x in eff["2026-10-01"]["lines"] if x["service"] == "Virtual Machines")["cost"] == 4.0
    with pytest.raises(ValueError):
        focus.aggregate(AZURE_ROWS, cost_metric="Unblended")


def test_multicloud_totals_by_provider(clouds):
    ds = analysis.load(clouds, "2026-10-01", "2026-10-02")
    out = analysis.summarize(ds, ["provider"])
    assert {r["provider"]: r["cost"] for r in out["rows"]} == {"azure": 12.5, "aws": 4.0, "gcp": 2.5}
    assert out["providers"] == ["aws", "azure", "gcp"]
    llm = analysis.llm_breakdown(ds)
    models = {m["model"]: m["cost"] for m in llm["models"]}
    assert models == {"gpt-4o-0513": 7.0, "Claude Sonnet 5.5": 3.0, "Gemini 2.5 Pro": 1.75}
    gem = next(m for m in llm["models"] if m["model"] == "Gemini 2.5 Pro")
    assert gem["byTokenType"] == {"input": 1.5, "cache_read": 0.25}
    net = analysis.summarize(ds, ["provider"], {"group": "networking"})
    assert net["rows"] == [{"provider": "azure", "cost": 0.5, "share": 1.0}]


def test_multicloud_projects_from_focus_tags(clouds):
    ds = analysis.load(clouds, "2026-10-01", "2026-10-02")
    p = {x["project"]: x["cost"] for x in analysis.project_costs(ds)["projects"]}
    assert p["chatbot"] == 8.0 and p["search"] == 1.5


def test_default_period_ends_at_the_earliest_provider(clouds):
    st = clouds.status()
    assert st["coverage"]["lastDay"] == "2026-10-02"
    assert set(st["providers"]) == {"aws", "azure", "gcp"}
    ds = analysis.load(clouds)
    assert ds.end == date(2026, 10, 2)


def test_mixed_currencies_need_fx(tmp_path, monkeypatch):
    aws = Store(str(tmp_path / "v1"))
    collect.run(aws, ce=FakeCE({"2026-10-01": [group("Amazon Simple Storage Service", "USE1-TimedStorage-ByteHrs", 1.0)]}),
                today=date(2026, 10, 2), lookback_days=1)
    gcp = Store(str(tmp_path / "v1" / "gcp"), provider="gcp")
    focus.ingest(gcp, [focus_row("2026-10-01", "Cloud Storage", "Standard Storage", 100.0, "Storage", currency="TWD")])
    both = MultiStore([aws, gcp])
    with pytest.raises(ValueError, match="FX_RATES"):
        analysis.load(both, "2026-10-01", "2026-10-01")
    monkeypatch.setenv("REPORTING_CURRENCY", "USD")
    monkeypatch.setenv("FX_RATES", '{"TWD": 0.031}')
    ds = analysis.load(both, "2026-10-01", "2026-10-01")
    assert ds.currency == "USD" and ds.fx["rates"] == {"TWD": 0.031}
    assert {r["provider"]: r["cost"] for r in analysis.summarize(ds, ["provider"])["rows"]} == {"gcp": 3.1, "aws": 1.0}


def test_tools_take_provider_filter(clouds):
    tools = {t.tool_name: t for t in build_tools(clouds)}
    out = json.loads(tools["cost_summary"](start_date="2026-10-01", end_date="2026-10-02", group_by="service", provider="gcp"))
    assert {r["service"] for r in out["rows"]} == {"Vertex AI", "Cloud Storage"}
    plot = json.loads(tools["plot_cost"](x="date", series="provider", start_date="2026-10-01", end_date="2026-10-02"))
    assert plot["days"][0]["by"] == {"azure": 12.0, "aws": 3.0, "gcp": 1.5}


def test_reingest_replaces_days(tmp_path):
    st = Store(str(tmp_path / "azure"), provider="azure")
    focus.ingest(st, AZURE_ROWS)
    focus.ingest(st, [focus_row("2026-10-02", "Bandwidth", "Standard Data Transfer Out", 0.9, "Networking")])
    ds = analysis.load(st, "2026-10-01", "2026-10-02")
    assert analysis.summarize(ds, ["date"])["rows"] == [
        {"date": "2026-10-01", "cost": 12.0, "share": 0.9302}, {"date": "2026-10-02", "cost": 0.9, "share": 0.0698}]


def test_focus_handler_finds_provider_and_run(monkeypatch):
    from finops import focus_handler
    monkeypatch.setenv("FOCUS_PROVIDERS", "azure,gcp")
    ev = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "landing/azure/2026-10/run1/manifest.json"}}},
                      {"s3": {"bucket": {"name": "b"}, "object": {"key": "landing/gcp/x/part-0.csv"}}}]}
    assert focus_handler._runs(ev) == [("azure", "b", "landing/azure/2026-10/run1/")]
    assert focus_handler._runs({"uri": "s3://b/landing/gcp/r2"}) == [("gcp", "b", "landing/gcp/r2/")]
    with pytest.raises(ValueError):
        focus_handler._provider_of("landing/ibm/x")
    for bad in ("landing/aws/run/manifest.json", "landing/oci/run/_SUCCESS"):  # aws = collector's store; oci not configured
        with pytest.raises(ValueError):
            focus_handler._runs({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": bad}}}]})
    with pytest.raises(ValueError):
        focus_handler._runs({"uri": "s3://b/landing/x/", "provider": "aws"})


def test_focus_handler_waits_for_all_manifest_files(monkeypatch, tmp_path):
    from finops import focus_handler

    class Body:
        def __init__(self, b): self.b = b
        def read(self): return self.b

    class FakeS3:
        def __init__(self, objects): self.objects = objects
        def get_paginator(self, _):
            objs = self.objects
            return type("P", (), {"paginate": lambda self, Bucket, Prefix, Delimiter=None: [
                {"Contents": [{"Key": k} for k in objs if k.startswith(Prefix) and "/" not in k[len(Prefix):]]}]})()
        def get_object(self, Bucket, Key):
            if Key not in self.objects:
                raise KeyError(Key)
            return {"Body": Body(self.objects[Key])}

    run = "landing/azure/exp/20261001-20261031/r1/"
    manifest = {"blobCount": 2, "blobs": [{"blobName": "exp/20261001-20261031/r1/part_0_0001.csv"},
                                          {"blobName": "exp/20261001-20261031/r1/part_0_0002.csv"}]}
    objects = {run + "manifest.json": json.dumps(manifest).encode(), run + "part_0_0001.csv": _csv(AZURE_ROWS[:2]),
               run + "part_9_old_leftover.csv": _csv(AZURE_ROWS),  # not in the manifest: must not be summed
               run + "older/part_0_0001.csv": _csv(AZURE_ROWS)}    # another run below: not listed at all
    s3 = FakeS3(objects)
    monkeypatch.setattr(focus_handler.boto3, "client", lambda name: s3)
    monkeypatch.setenv("DATA_URI", str(tmp_path / "v1"))
    monkeypatch.setenv("FOCUS_PROVIDERS", "azure")
    ev = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": run + "manifest.json"}}}]}
    with pytest.raises(focus_handler.IncompleteRun):  # raise -> S3 async invocation is retried
        focus_handler.handler(ev, None)
    objects[run + "part_0_0002.csv"] = _csv(AZURE_ROWS[2:])
    out = focus_handler.handler(ev, None)["runs"][0]
    assert out["files"] == 2 and out["rows"] == 5 and out["provider"] == "azure"
    assert Store(str(tmp_path / "v1" / "azure"), provider="azure").status()["coverage"]["lastDay"] == "2026-10-02"


def test_stale_provider_does_not_freeze_the_default_window(tmp_path):
    aws = Store(str(tmp_path / "v1"))
    collect.run(aws, ce=FakeCE({"2026-10-09": [group("Amazon Simple Storage Service", "USE1-TimedStorage-ByteHrs", 1.0)]}),
                today=date(2026, 10, 10), lookback_days=1)
    gcp = Store(str(tmp_path / "v1" / "gcp"), provider="gcp")
    focus.ingest(gcp, [focus_row("2026-08-01", "Cloud Storage", "Standard Storage", 1.0, "Storage")])
    both = MultiStore([aws, gcp])
    st = both.status()
    assert st["coverage"]["lastDay"] == "2026-10-09" and st["staleProviders"] == {"gcp": "2026-08-01"}
    ds = analysis.load(both)
    assert ds.end == date(2026, 10, 9) and ds.meta()["staleProviders"] == {"gcp": "2026-08-01"}
    assert analysis.summarize(ds, ["provider"])["total"] == 1.0


def test_out_of_period_corrections_do_not_overwrite_other_months(tmp_path):
    st = Store(str(tmp_path / "azure"), provider="azure")
    sep = ("2026-09-01", "2026-10-01")
    oct_ = ("2026-10-01", "2026-11-01")
    focus.ingest(st, [focus_row("2026-09-15", "Virtual Machines", "D4s v5", 100.0, period=sep)])
    run = [focus_row("2026-10-01", "Virtual Machines", "D4s v5", 5.0, period=oct_),
           focus_row("2026-09-15", "Virtual Machines", "D4s v5", -2.0, period=oct_, charge="Adjustment")]
    out = focus.ingest(st, run)
    focus.ingest(st, run)  # re-ingesting the same run is idempotent
    assert out["outOfPeriodRows"] == 1
    ds = analysis.load(st, "2026-09-01", "2026-10-31")
    by_day = {r["date"]: r["cost"] for r in analysis.summarize(ds, ["date"])["rows"]}
    assert by_day == {"2026-09-15": 100.0, "2026-10-01": 3.0}


def test_focus_project_costs_keep_categories(clouds):
    ds = analysis.load(clouds, "2026-10-01", "2026-10-02")
    chatbot = next(p for p in analysis.project_costs(ds)["projects"] if p["project"] == "chatbot")
    assert chatbot["byCategory"] == {"compute": 5.0, "llm": 3.0}


def test_provider_filter_reads_only_that_provider(tmp_path):
    aws = Store(str(tmp_path / "v1"))
    collect.run(aws, ce=FakeCE({"2026-10-01": [group("Amazon Simple Storage Service", "USE1-TimedStorage-ByteHrs", 1.0)]}),
                today=date(2026, 10, 2), lookback_days=1)
    gcp = Store(str(tmp_path / "v1" / "gcp"), provider="gcp")
    focus.ingest(gcp, [focus_row("2026-10-01", "Cloud Storage", "Standard Storage", 100.0, "Storage", currency="TWD")])
    tools = {t.tool_name: t for t in build_tools(MultiStore([aws, gcp]))}
    assert "FX_RATES" in json.loads(tools["cost_summary"](start_date="2026-10-01", end_date="2026-10-01"))["error"]
    ok = json.loads(tools["cost_summary"](start_date="2026-10-01", end_date="2026-10-01", provider="aws", group_by="provider"))
    assert ok["total"] == 1.0 and ok["currency"] == "USD"
    assert "no data source" in json.loads(tools["cost_summary"](provider="oci"))["error"]


def test_fx_env_is_validated(monkeypatch):
    monkeypatch.setenv("FX_RATES", '{"TWD": null}')
    with pytest.raises(ValueError):
        analysis.fx_rates_from_env()
    monkeypatch.setenv("FX_RATES", '{"twd": 0.031}')
    monkeypatch.setenv("REPORTING_CURRENCY", " usd ")
    assert analysis.fx_rates_from_env() == ("USD", {"TWD": 0.031})


def test_status_action_describes_the_setup(clouds, monkeypatch):
    import main
    st = main.system_status(clouds)
    by = {p["provider"]: p for p in st["providers"]}
    assert set(by) == {"aws", "azure", "gcp"}
    assert by["aws"]["source"] == "costExplorer" and by["azure"]["source"] == "focus"
    assert by["azure"]["costMetric"] == "BilledCost" and by["gcp"]["hasData"]
    assert "plot_cost" in st["tools"] and st["coverage"]["lastDay"] == "2026-10-02"

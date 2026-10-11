"""Strands tools over the stored cost data.  Every tool returns JSON text produced by ``finops.analysis``.

Only ``month_end_projection(use_cost_explorer_forecast=True)`` makes a paid Cost Explorer request (USD 0.01);
it is capped per session by ``MAX_LIVE_CE_CALLS``.  Everything else reads the collected data in S3, for every
configured cloud provider (``DATA_PROVIDERS``).

Contract with the web UI: a tool result is a JSON object; ``{"error": ...}`` on failure.  ``plot_cost`` results
carry a ``chart`` spec the UI renders generically.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from strands import tool

from finops import analysis
from finops.collect import DEFAULT_TAG_KEYS, tag_keys_from_env
from finops.store import MultiStore, Store

log = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _guard(fn: Callable[..., Any]) -> Callable[..., str]:
    """Turn every failure into a JSON ``{"error": ...}`` the model can act on (and the UI can show) instead of a
    failed tool call with free text."""
    @functools.wraps(fn)  # keeps the signature, annotations and docstring strands builds the tool spec from
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return _json(fn(*args, **kwargs))
        except ValueError as e:
            return _json({"error": str(e)})
        except Exception as e:  # noqa: BLE001 - S3/API errors, bugs: still a well-formed result
            log.exception("tool %s failed", fn.__name__)
            return _json({"error": f"{type(e).__name__}: {str(e)[:300]}"})
    return wrapper


def _filters(**kw: str) -> dict[str, str]:
    return {k: v for k, v in kw.items() if v}


def _dims(group_by: str) -> list[str]:
    return [g.strip() for g in (group_by or "").split(",") if g.strip()]


def build_tools(store: Store | MultiStore | None = None, ce_client: Any = None, tagging_client: Any = None) -> list:
    store = store or MultiStore.from_env()
    live_budget = {"left": int(os.environ.get("MAX_LIVE_CE_CALLS", "3") or 3)}
    clients: dict[str, Any] = {"ce": ce_client, "tagging": tagging_client}

    def client(name: str) -> Any:
        if clients[name] is None:
            import boto3

            clients[name] = boto3.client("ce", region_name="us-east-1") if name == "ce" else boto3.client("resourcegroupstaggingapi")
        return clients[name]

    @tool
    @_guard
    def data_status() -> dict:
        """When the cost data was last collected, which days it covers (per cloud provider when there are several),
        which days are still estimated, and how projects can be grouped (cost category, tag, or usage allocation)."""
        return store.status() or {"error": "no data collected yet - run the collector Lambda / FOCUS ingestion"}

    @tool
    @_guard
    def cost_summary(start_date: str = "", end_date: str = "", group_by: str = "category", provider: str = "",
                     group: str = "", category: str = "", service: str = "", model: str = "", region: str = "",
                     account: str = "", top_n: int = 20) -> dict:
        """Total cost for a period, broken down by one or more dimensions.  For a chart over time prefer plot_cost.

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            group_by: comma-separated dimensions: provider, account, group, category, subcategory, service,
                usage_type, region, model, token_type, date, month. Example: "category,service".  With date or
                month plus other dimensions, every day/month is kept and top_n limits the other dimensions' values
                (max 10, rest summed as "(other)").
            provider: filter: aws | azure | gcp | oci
            group: filter: llm | infrastructure | networking | platform_services | support_and_tax | other
            category: filter: llm, ai_agent_platform, compute, storage, database, networking, observability,
                security_and_management, analytics, app_integration, support, tax, other
            service: filter on the exact Cost Explorer service name
            model: filter on an LLM model name, e.g. "Claude Sonnet 5.5"
            region: filter on a region code, e.g. "us-east-1" ("global" for support/tax)
            account: filter on a sub-account: AWS account id, Azure subscription id, Google Cloud project id
            top_n: number of rows to return (the rest is summed in notShown)
        """
        ds = analysis.load(store, start_date, end_date, provider=provider)
        return analysis.summarize(ds, _dims(group_by), _filters(provider=provider, group=group, category=category,
                                                                 service=service, model=model, region=region,
                                                                 account=account), top_n=top_n)

    @tool
    @_guard
    def daily_trend(start_date: str = "", end_date: str = "", group_by: str = "group", provider: str = "",
                    group: str = "", category: str = "", service: str = "", model: str = "", region: str = "",
                    top_n: int = 6) -> dict:
        """Cost per day for a period, split into the top series of one dimension (others summed as "(other)").

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            group_by: one dimension: provider, account, group, category, service, model, region, usage_type, token_type
            provider: optional filter: aws | azure | gcp | oci
            group: optional group filter, e.g. llm
            category: optional category filter
            service: optional exact service-name filter
            model: optional LLM model filter
            region: optional region filter
            top_n: number of series (1-10)
        """
        ds = analysis.load(store, start_date, end_date, provider=provider)
        return analysis.trend(ds, group_by, _filters(provider=provider, group=group, category=category, service=service,
                                                     model=model, region=region), top_n=max(1, min(top_n, 10)))

    @tool
    @_guard
    def plot_cost(x: str = "date", series: str = "group", start_date: str = "", end_date: str = "",
                  provider: str = "", group: str = "", category: str = "", service: str = "", model: str = "",
                  region: str = "", account: str = "", top_n: int = 6, title: str = "") -> dict:
        """Draw a cost chart in the user's UI.  Use it whenever the user asks to plot, chart, graph, show or
        visualize cost.  The UI renders the returned chart from these exact numbers; do not draw charts in text.

        x = date or month  -> stacked columns over time, one stack per day/month, split by `series`.
        x = any other dimension -> horizontal bars per value of x (top 15), each split by `series`.

        Args:
            x: date | month | provider | account | group | category | subcategory | service | usage_type | region | model | token_type
            series: dimension to split/color by (same list, not date/month), or "" for a single total series
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            provider: optional filter: aws | azure | gcp | oci
            group: optional filter: llm | infrastructure | networking | platform_services | support_and_tax | other
            category: optional category filter
            service: optional exact service-name filter
            model: optional LLM model filter
            region: optional region filter
            account: optional sub-account filter
            top_n: series shown (1-10), the rest summed as "(other)"
            title: chart title; default derived from x, series and filters
        """
        if series in analysis.TIME_DIMENSIONS:
            raise ValueError("series cannot be date or month; put time on x")
        if series and series == x:
            raise ValueError("series must differ from x")
        filters = _filters(provider=provider, group=group, category=category, service=service, model=model,
                           region=region, account=account)
        ds = analysis.load(store, start_date, end_date, provider=provider)
        n = max(1, min(top_n, 10))
        if x in analysis.TIME_DIMENSIONS:
            data = analysis.trend(ds, series, filters, top_n=n, bucket=x)
            kind = "stacked_columns"
        else:
            data = analysis.breakdown(ds, x, series, filters, top_n=15, series_top=n)
            kind = "bars"
        auto = f"{'Daily' if x == 'date' else 'Monthly' if x == 'month' else ''} cost{f' by {series}' if series else ''}"
        if x not in analysis.TIME_DIMENSIONS:
            auto = f"Cost by {x}{f' and {series}' if series else ''}"
        where = ", ".join(f"{k}: {v}" for k, v in filters.items())
        auto = auto.strip().replace("_", " ")
        chart = {"kind": kind, "x": x, "series": series, "title": title or (auto + (f" ({where})" if where else ""))}
        return {"chart": chart, **data}

    @tool
    @_guard
    def llm_costs(start_date: str = "", end_date: str = "", provider: str = "") -> dict:
        """LLM (Bedrock model inference) cost per model, split by token type (input, output, cache_read,
        cache_write, provisioned) and by global vs regional inference, with its share of the account total.
        Covers Bedrock, Azure OpenAI / Foundry and Vertex AI token meters.

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            provider: optional filter: aws | azure | gcp | oci
        """
        return analysis.llm_breakdown(analysis.load(store, start_date, end_date, provider=provider))

    @tool
    @_guard
    def project_costs(start_date: str = "", end_date: str = "", provider: str = "") -> dict:
        """Cost per project and category.  Uses the project cost category / cost allocation tag when one is active;
        otherwise splits AgentCore Runtime cost per runtime by measured vCPU/memory usage and leaves the rest
        "(unallocated)".  allocationCoverage tells how much of the cost could be attributed.  Azure / Google Cloud
        cost is attributed by the FOCUS Tags of each line.

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            provider: optional filter: aws | azure | gcp | oci
        """
        return analysis.project_costs(analysis.load(store, start_date, end_date, provider=provider))

    @tool
    @_guard
    def compare_periods(baseline_start: str, baseline_end: str, current_start: str, current_end: str,
                        group_by: str = "service", provider: str = "", group: str = "", category: str = "",
                        top_n: int = 15) -> dict:
        """Compare two periods and list what changed most (sorted by change in daily average).

        Args:
            baseline_start: first day of the earlier period, YYYY-MM-DD
            baseline_end: last day of the earlier period, YYYY-MM-DD (inclusive)
            current_start: first day of the later period, YYYY-MM-DD
            current_end: last day of the later period, YYYY-MM-DD (inclusive)
            group_by: comma-separated dimensions, e.g. "service", "provider" or "category,usage_type"
            provider: optional filter: aws | azure | gcp | oci
            group: optional group filter, e.g. llm
            category: optional category filter
            top_n: rows to return
        """
        a = analysis.load(store, baseline_start, baseline_end, provider=provider)
        b = analysis.load(store, current_start, current_end, provider=provider)
        return analysis.compare(a, b, _dims(group_by), top_n=top_n,
                                filters=_filters(provider=provider, group=group, category=category))

    @tool
    @_guard
    def find_anomalies(start_date: str = "", end_date: str = "", group_by: str = "category", provider: str = "",
                       min_delta: float = 0.5) -> dict:
        """Days on which a category/service/model cost jumped above its trailing 7-day mean (more than 2 standard
        deviations, at least min_delta and +50%).  Support and tax are ignored (billed monthly on the 1st).

        Args:
            start_date: first day, YYYY-MM-DD. Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            group_by: provider, account, category, service, model, usage_type or region
            provider: optional filter: aws | azure | gcp | oci
            min_delta: smallest increase over the mean worth reporting, in account currency
        """
        return analysis.anomalies(analysis.load(store, start_date, end_date, default_days=45, provider=provider), group_by,
                                  min_delta=min_delta, filters=_filters(provider=provider))

    @tool
    @_guard
    def month_end_projection(month: str = "", use_cost_explorer_forecast: bool = False) -> dict:
        """Month-to-date cost and two month-end estimates (MTD daily rate, last-7-day rate).  Support and tax are
        treated as fixed monthly charges.  use_cost_explorer_forecast=true adds AWS's own forecast for the current
        month (one paid Cost Explorer request; limited per session).

        Args:
            month: YYYY-MM. Default: the month of the last collected day.
            use_cost_explorer_forecast: also call Cost Explorer GetCostForecast (current month only)
        """
        out = analysis.projection(store, month or None)
        if use_cost_explorer_forecast:
            out["costExplorerForecast"] = _ce_forecast()
        return out

    def _ce_forecast() -> dict:
        if live_budget["left"] <= 0:
            return {"error": "live Cost Explorer budget for this session is used up"}
        live_budget["left"] -= 1
        today = datetime.now(timezone.utc).date()
        nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        try:
            resp = client("ce").get_cost_forecast(
                TimePeriod={"Start": today.isoformat(), "End": nxt.isoformat()},
                Metric="UNBLENDED_COST", Granularity="MONTHLY", PredictionIntervalLevel=80)
        except Exception as e:  # e.g. DataUnavailableException with too little history
            return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        total = resp.get("Total", {})
        rng = (resp.get("ForecastResultsByTime") or [{}])[0]
        return {"remainingMonthForecast": total.get("Amount"), "unit": total.get("Unit"),
                "period": {"start": today.isoformat(), "endExclusive": nxt.isoformat()},
                "lower80": rng.get("PredictionIntervalLowerBound"), "upper80": rng.get("PredictionIntervalUpperBound"),
                "note": "Covers today through month end only; add month-to-date for the month total."}

    @tool
    @_guard
    def project_resources(project: str = "", limit: int = 100) -> dict:
        """AWS only: resources in this account/region and the project they are tagged with (PROJECT_TAG_KEYS).
        Shows what each project owns even where its cost cannot be split.

        Args:
            project: only resources of this project (case-insensitive); empty = summary of all projects
            limit: maximum resources to list
        """
        keys = tag_keys_from_env() or DEFAULT_TAG_KEYS
        params: dict[str, Any] = {}
        per_project: dict[str, dict[str, int]] = {}
        listed, untagged = [], 0
        pages = 0
        while pages < 20:
            resp = client("tagging").get_resources(**params)
            pages += 1
            for r in resp.get("ResourceTagMappingList", []):
                tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
                key = next((k for k in keys if tags.get(k)), None)
                arn = r["ResourceARN"]
                rtype = ":".join(arn.split(":")[2:3]) + "/" + (arn.split(":")[5].split("/")[0] if arn.count(":") >= 5 else "")
                if not key:
                    untagged += 1
                    continue
                name = tags[key]
                per_project.setdefault(name, {}).setdefault(rtype, 0)
                per_project[name][rtype] += 1
                if project and name.lower() == project.lower() and len(listed) < limit:
                    listed.append({"arn": arn, "tagKey": key})
            params["PaginationToken"] = resp.get("PaginationToken")
            if not params["PaginationToken"]:
                break
        return {"tagKeys": list(keys), "projects": per_project, "resourcesWithoutProjectTag": untagged,
                **({"resources": listed} if project else {})}

    return [data_status, cost_summary, daily_trend, plot_cost, llm_costs, project_costs, compare_periods,
            find_anomalies, month_end_projection, project_resources]


# ----------------------------------------------------------------------------------------------
# guardrail: reject arguments a tool does not declare
# ----------------------------------------------------------------------------------------------
class StrictToolArgs:
    """Strands silently drops arguments a tool does not declare (e.g. ``daily_trend(group="llm")`` before it had a
    ``group`` filter), which returns unfiltered numbers that look filtered.  Cancel such calls with an error the
    model can correct instead."""

    def register_hooks(self, registry: Any, **_: Any) -> None:
        from strands.hooks import BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._check)

    @staticmethod
    def _check(event: Any) -> None:
        tool_ = event.selected_tool
        if tool_ is None:
            return
        allowed = set((tool_.tool_spec.get("inputSchema", {}).get("json", {}).get("properties") or {}).keys())
        given = set((event.tool_use.get("input") or {}).keys())
        unknown = sorted(given - allowed)
        if unknown:
            event.cancel_tool = _json({"error": f"{tool_.tool_name} has no argument(s) {', '.join(unknown)}; "
                                                f"valid arguments: {', '.join(sorted(allowed)) or 'none'}"})

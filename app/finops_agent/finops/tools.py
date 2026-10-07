"""Strands tools over the stored cost data.  Every tool returns JSON text produced by ``finops.analysis``.

Only ``month_end_projection(use_cost_explorer_forecast=True)`` makes a paid Cost Explorer request (USD 0.01);
it is capped per session by ``MAX_LIVE_CE_CALLS``.  Everything else reads the collected data in S3.
"""

from __future__ import annotations

import functools
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from strands import tool

from finops import analysis
from finops.collect import DEFAULT_TAG_KEYS, tag_keys_from_env
from finops.store import Store


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _guard(fn: Callable[..., Any]) -> Callable[..., str]:
    """Turn input errors into a message the model can act on instead of a failed tool call."""
    @functools.wraps(fn)  # keeps the signature, annotations and docstring strands builds the tool spec from
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return _json(fn(*args, **kwargs))
        except ValueError as e:
            return _json({"error": str(e)})
    return wrapper


def _filters(**kw: str) -> dict[str, str]:
    return {k: v for k, v in kw.items() if v}


def _dims(group_by: str) -> list[str]:
    return [g.strip() for g in (group_by or "").split(",") if g.strip()]


def build_tools(store: Store | None = None, ce_client: Any = None, tagging_client: Any = None) -> list:
    store = store or Store.from_env()
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
        """When the cost data was last collected, which days it covers, which days Cost Explorer still marks as
        estimated, and how projects can be grouped (cost category, cost allocation tag, or usage allocation)."""
        return store.status() or {"error": "no data collected yet - run the collector Lambda"}

    @tool
    @_guard
    def cost_summary(start_date: str = "", end_date: str = "", group_by: str = "category", group: str = "",
                     category: str = "", service: str = "", model: str = "", region: str = "", top_n: int = 20) -> dict:
        """Total cost for a period, broken down by one or more dimensions.

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            group_by: comma-separated dimensions: group, category, subcategory, service, usage_type, region,
                model, token_type, date, month. Example: "category,service".
            group: filter: llm | infrastructure | networking | platform_services | support_and_tax | other
            category: filter: llm, ai_agent_platform, compute, storage, database, networking, observability,
                security_and_management, analytics, app_integration, support, tax, other
            service: filter on the exact Cost Explorer service name
            model: filter on an LLM model name, e.g. "Claude Sonnet 5.5"
            region: filter on a region code, e.g. "us-east-1" ("global" for support/tax)
            top_n: number of rows to return (the rest is summed in notShown)
        """
        ds = analysis.load(store, start_date, end_date)
        return analysis.summarize(ds, _dims(group_by), _filters(group=group, category=category, service=service,
                                                                 model=model, region=region), top_n=top_n)

    @tool
    @_guard
    def daily_trend(start_date: str = "", end_date: str = "", group_by: str = "group", category: str = "",
                    service: str = "") -> dict:
        """Cost per day for a period, split into the top series of one dimension (others summed as "(other)").

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            group_by: one dimension: group, category, service, model, region, usage_type, token_type
            category: optional category filter
            service: optional exact service-name filter
        """
        ds = analysis.load(store, start_date, end_date)
        return analysis.trend(ds, group_by, _filters(category=category, service=service))

    @tool
    @_guard
    def llm_costs(start_date: str = "", end_date: str = "") -> dict:
        """LLM (Bedrock model inference) cost per model, split by token type (input, output, cache_read,
        cache_write, provisioned) and by global vs regional inference, with its share of the account total.

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
        """
        return analysis.llm_breakdown(analysis.load(store, start_date, end_date))

    @tool
    @_guard
    def project_costs(start_date: str = "", end_date: str = "") -> dict:
        """Cost per project and category.  Uses the project cost category / cost allocation tag when one is active;
        otherwise splits AgentCore Runtime cost per runtime by measured vCPU/memory usage and leaves the rest
        "(unallocated)".  allocationCoverage tells how much of the cost could be attributed.

        Args:
            start_date: first day, YYYY-MM-DD (inclusive). Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
        """
        return analysis.project_costs(analysis.load(store, start_date, end_date))

    @tool
    @_guard
    def compare_periods(baseline_start: str, baseline_end: str, current_start: str, current_end: str,
                        group_by: str = "service", top_n: int = 15) -> dict:
        """Compare two periods and list what changed most (sorted by change in daily average).

        Args:
            baseline_start: first day of the earlier period, YYYY-MM-DD
            baseline_end: last day of the earlier period, YYYY-MM-DD (inclusive)
            current_start: first day of the later period, YYYY-MM-DD
            current_end: last day of the later period, YYYY-MM-DD (inclusive)
            group_by: comma-separated dimensions, e.g. "service" or "category,usage_type"
            top_n: rows to return
        """
        a = analysis.load(store, baseline_start, baseline_end)
        b = analysis.load(store, current_start, current_end)
        return analysis.compare(a, b, _dims(group_by), top_n=top_n)

    @tool
    @_guard
    def find_anomalies(start_date: str = "", end_date: str = "", group_by: str = "category", min_delta: float = 0.5) -> dict:
        """Days on which a category/service/model cost jumped above its trailing 7-day mean (more than 2 standard
        deviations, at least min_delta and +50%).  Support and tax are ignored (billed monthly on the 1st).

        Args:
            start_date: first day, YYYY-MM-DD. Default: 30 days before end_date.
            end_date: last day, YYYY-MM-DD (inclusive). Default: the last collected day.
            group_by: category, service, model, usage_type or region
            min_delta: smallest increase over the mean worth reporting, in account currency
        """
        return analysis.anomalies(analysis.load(store, start_date, end_date, default_days=45), group_by, min_delta=min_delta)

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
        """Resources in this account/region and the project they are tagged with (tag keys from PROJECT_TAG_KEYS).
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

    return [data_status, cost_summary, daily_trend, llm_costs, project_costs, compare_periods, find_anomalies,
            month_end_projection, project_resources]

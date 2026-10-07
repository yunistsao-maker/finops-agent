"""Daily collection: Cost Explorer (+ CloudWatch usage, + resource tags) -> month documents in the store.

Each run re-reads a rolling window (default: the last 7 complete UTC days) because Cost Explorer keeps revising
recent days; the days of the window are replaced, older days are kept.  A run costs 2-3 Cost Explorer requests
(USD 0.01 each): GetCostAndUsage by service x usage type, GetTags, and - only when a project dimension is
available - GetCostAndUsage by that dimension x service.

Project grouping, best source first:
  1. a Cost Category (``COST_CATEGORY``) - defined in the payer account, can combine tags, accounts and services
  2. the first cost allocation tag of ``PROJECT_TAG_KEYS`` that is active in the period
  3. usage allocation: AgentCore Runtime vCPU/memory cost is split per runtime by its CloudWatch vCPU-hours /
     GB-hours, and a runtime maps to a project by its tag (or its ``<Project>_<agent>`` name).  Everything else
     stays "unallocated".  This works in a linked account where cost allocation tags cannot be activated.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from finops.store import SCHEMA_VERSION, Store

log = logging.getLogger(__name__)

DEFAULT_TAG_KEYS = ("project", "Project", "agentcore:project-name", "app")
AGENTCORE_NS = "AWS/Bedrock-AgentCore"
RUNTIME_METRICS = {"CPUUsed-vCPUHours": "vcpuHours", "MemoryUsed-GBHours": "gbHours"}
MAX_WINDOW_DAYS = 400


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _amount(metrics: dict, name: str) -> tuple[float, str]:
    m = metrics.get(name) or {}
    return float(m.get("Amount") or 0.0), m.get("Unit") or ""


def _cost_and_usage(ce: Any, start: date, end: date, group_by: list[dict], metrics: list[str]) -> tuple[list[dict], int]:
    """All ResultsByTime of one GetCostAndUsage query (paginated) and the number of requests made."""
    params: dict[str, Any] = {
        "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
        "Granularity": "DAILY",
        "Metrics": metrics,
        "GroupBy": group_by,
    }
    results, requests = [], 0
    while True:
        resp = ce.get_cost_and_usage(**params)
        requests += 1
        results.extend(resp.get("ResultsByTime", []))
        token = resp.get("NextPageToken")
        if not token:
            return results, requests
        params["NextPageToken"] = token


def fetch_lines(ce: Any, start: date, end: date) -> tuple[dict[str, dict], str, int]:
    """{day: {"estimated": bool, "lines": [...]}} for [start, end), the currency, and the request count."""
    results, requests = _cost_and_usage(
        ce, start, end,
        [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        ["UnblendedCost", "UsageQuantity"],
    )
    days: dict[str, dict] = {}
    currency = "USD"
    for r in results:
        day = r["TimePeriod"]["Start"]
        entry = days.setdefault(day, {"estimated": bool(r.get("Estimated")), "lines": []})
        entry["estimated"] = entry["estimated"] or bool(r.get("Estimated"))
        for g in r.get("Groups", []):
            cost, unit = _amount(g["Metrics"], "UnblendedCost")
            usage, usage_unit = _amount(g["Metrics"], "UsageQuantity")
            if unit:
                currency = unit
            if abs(cost) < 1e-10:
                continue
            service, usage_type = (g["Keys"] + ["", ""])[:2]
            entry["lines"].append({"service": service, "usageType": usage_type, "cost": round(cost, 10),
                                   "usage": round(usage, 10), "unit": usage_unit})
    return days, currency, requests


def fetch_project_lines(ce: Any, start: date, end: date, source: str, key: str) -> tuple[dict[str, dict], int]:
    """{day: {"source", "key", "lines": [{"value", "service", "cost"}]}} grouped by a tag or cost category x service."""
    gtype = "COST_CATEGORY" if source == "costCategory" else "TAG"
    results, requests = _cost_and_usage(
        ce, start, end, [{"Type": gtype, "Key": key}, {"Type": "DIMENSION", "Key": "SERVICE"}], ["UnblendedCost"]
    )
    out: dict[str, dict] = {}
    for r in results:
        day = r["TimePeriod"]["Start"]
        entry = out.setdefault(day, {"source": source, "key": key, "lines": []})
        for g in r.get("Groups", []):
            cost, _ = _amount(g["Metrics"], "UnblendedCost")
            if abs(cost) < 1e-10:
                continue
            raw, service = (g["Keys"] + ["", ""])[:2]
            value = raw.split("$", 1)[1] if "$" in raw else raw  # "project$alpha" -> "alpha"; "project$" -> untagged
            entry["lines"].append({"value": value, "service": service, "cost": round(cost, 10)})
    return out, requests


def active_tag_keys(ce: Any, start: date, end: date) -> list[str]:
    resp = ce.get_tags(TimePeriod={"Start": start.isoformat(), "End": end.isoformat()})
    return list(resp.get("Tags", []))


# --------------------------------------------------------------------------------------------------
# AgentCore Runtime usage (CloudWatch) and runtime -> project mapping (resource tags)
# --------------------------------------------------------------------------------------------------
def runtime_name(arn: str) -> str:
    """``.../runtime/LlmopsResearch_llmops_research-4ArzQH3bwJ`` -> ``LlmopsResearch_llmops_research``."""
    tail = arn.rsplit("/", 1)[-1]
    return tail.rsplit("-", 1)[0] if "-" in tail else tail


def project_from_name(name: str) -> str:
    """AgentCore CLI names runtimes ``<Project>_<agent>`` (harnesses ``harness_<Project>_<agent>``)."""
    n = name[len("harness_"):] if name.startswith("harness_") else name
    return n.split("_", 1)[0] if "_" in n else n


def resource_tags(tagging: Any) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    params: dict[str, Any] = {"ResourceTypeFilters": ["bedrock-agentcore"]}
    while True:
        resp = tagging.get_resources(**params)
        for r in resp.get("ResourceTagMappingList", []):
            out[r["ResourceARN"]] = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
        token = resp.get("PaginationToken")
        if not token:
            return out
        params["PaginationToken"] = token


def runtime_project(arn: str, tags: dict[str, dict[str, str]], tag_keys: tuple[str, ...]) -> tuple[str, str]:
    """(project, source): the runtime's own tag, else a tag on one of its sub-resources (endpoints), else its name."""
    candidates = [tags.get(arn, {})] + [t for a, t in sorted(tags.items()) if a.startswith(arn + "/")]
    for key in tag_keys:
        for t in candidates:
            if t.get(key):
                return t[key], "tag"
    return project_from_name(runtime_name(arn)), "name"


def fetch_runtime_usage(cw: Any, tagging: Any, start: date, end: date, tag_keys: tuple[str, ...]) -> dict[str, list[dict]]:
    """{day: [{"runtime", "arn", "project", "projectSource", "vcpuHours", "gbHours"}]} for [start, end)."""
    series: list[tuple[str, str]] = []  # (metric, runtime arn)
    for metric in RUNTIME_METRICS:
        params: dict[str, Any] = {"Namespace": AGENTCORE_NS, "MetricName": metric,
                                  "Dimensions": [{"Name": "Service", "Value": "AgentCore.Runtime"}]}
        while True:
            resp = cw.list_metrics(**params)
            for m in resp.get("Metrics", []):
                dims = {d["Name"]: d["Value"] for d in m.get("Dimensions", [])}
                if set(dims) == {"Service", "Resource"}:
                    series.append((metric, dims["Resource"]))
            token = resp.get("NextToken")
            if not token:
                break
            params["NextToken"] = token
    if not series:
        return {}

    tags = resource_tags(tagging) if tagging is not None else {}
    usage: dict[tuple[str, str], dict] = {}  # (day, arn) -> record
    t0 = datetime.combine(start, time.min, tzinfo=timezone.utc)
    t1 = datetime.combine(end, time.min, tzinfo=timezone.utc)
    for i in range(0, len(series), 500):  # GetMetricData takes at most 500 queries
        chunk = series[i:i + 500]
        queries = [{
            "Id": f"m{j}",
            "MetricStat": {"Metric": {"Namespace": AGENTCORE_NS, "MetricName": metric,
                                      "Dimensions": [{"Name": "Service", "Value": "AgentCore.Runtime"},
                                                     {"Name": "Resource", "Value": arn}]},
                           "Period": 86400, "Stat": "Sum"},
            "ReturnData": True,
        } for j, (metric, arn) in enumerate(chunk)]
        params = {"MetricDataQueries": queries, "StartTime": t0, "EndTime": t1, "ScanBy": "TimestampAscending"}
        while True:
            resp = cw.get_metric_data(**params)
            for res in resp.get("MetricDataResults", []):
                metric, arn = chunk[int(res["Id"][1:])]
                for ts, value in zip(res.get("Timestamps", []), res.get("Values", [])):
                    day = ts.astimezone(timezone.utc).date().isoformat()
                    rec = usage.get((day, arn))
                    if rec is None:
                        project, source = runtime_project(arn, tags, tag_keys)
                        rec = usage[(day, arn)] = {"runtime": runtime_name(arn), "arn": arn, "project": project,
                                                   "projectSource": source, "vcpuHours": 0.0, "gbHours": 0.0}
                    rec[RUNTIME_METRICS[metric]] += float(value)
            token = resp.get("NextToken")
            if not token:
                break
            params["NextToken"] = token

    out: dict[str, list[dict]] = {}
    for (day, _), rec in sorted(usage.items()):
        rec["vcpuHours"], rec["gbHours"] = round(rec["vcpuHours"], 8), round(rec["gbHours"], 8)
        out.setdefault(day, []).append(rec)
    return out


# --------------------------------------------------------------------------------------------------
# one collection run
# --------------------------------------------------------------------------------------------------
def window(today: date, lookback_days: int, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    """[start, end) of complete UTC days.  ``end`` defaults to today (today itself is incomplete)."""
    end = min(end or today, today)
    start = start or end - timedelta(days=lookback_days)
    if start >= end:
        raise ValueError(f"empty collection window {start}..{end}")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise ValueError(f"collection window of {(end - start).days} days exceeds {MAX_WINDOW_DAYS}")
    return start, end


def run(store: Store, *, ce: Any, cw: Any = None, tagging: Any = None, account_id: str = "",
        today: date | None = None, lookback_days: int = 7, start: date | None = None, end: date | None = None,
        tag_keys: tuple[str, ...] = DEFAULT_TAG_KEYS, cost_category: str = "") -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    start, end = window(today, lookback_days, start, end)
    log.info("collecting %s .. %s (exclusive)", start, end)

    days, currency, requests = fetch_lines(ce, start, end)

    active = active_tag_keys(ce, start, end)
    requests += 1
    grouping: dict[str, Any] = {"checkedTagKeys": list(tag_keys), "activeTagKeys": active}
    project_days: dict[str, dict] = {}
    if cost_category:
        grouping.update(mode="costCategory", key=cost_category)
    else:
        key = next((k for k in tag_keys if k in active), None)
        grouping.update(mode="tag" if key else "usageAllocation", key=key)
    if grouping["key"]:
        source = "costCategory" if grouping["mode"] == "costCategory" else "tag"
        project_days, n = fetch_project_lines(ce, start, end, source, grouping["key"])
        requests += n

    runtime_days: dict[str, list[dict]] = {}
    if cw is not None:
        try:
            runtime_days = fetch_runtime_usage(cw, tagging, start, end, tag_keys)
        except Exception as e:  # usage allocation is an extra: a CloudWatch problem must not lose the cost data
            log.warning("runtime usage not collected: %s", e)
            grouping["runtimeUsageError"] = str(e)[:300]

    now = _iso_now()
    by_month: dict[str, list[str]] = {}
    for day in days:
        by_month.setdefault(day[:7], []).append(day)
    for month, month_days in sorted(by_month.items()):
        doc = store.month(month)
        doc.update(schemaVersion=SCHEMA_VERSION, accountId=account_id, currency=currency, updatedAt=now)
        for day in month_days:
            doc["days"][day] = {
                "estimated": days[day]["estimated"],
                "lines": days[day]["lines"],
                "projectLines": project_days.get(day),
                "runtimeUsage": runtime_days.get(day, []),
            }
        store.put_month(doc)

    prev = store.status() or {}
    first = min([d for d in (prev.get("coverage", {}).get("firstDay"), start.isoformat()) if d])
    last = max([d for d in (prev.get("coverage", {}).get("lastDay"), (end - timedelta(days=1)).isoformat()) if d])
    status = {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": now,
        "accountId": account_id,
        "currency": currency,
        "lastWindow": {"start": start.isoformat(), "endExclusive": end.isoformat()},
        "coverage": {"firstDay": first, "lastDay": last},
        "estimatedDays": sorted(d for d, v in days.items() if v["estimated"]),
        "projectGrouping": grouping,
        "costExplorerRequests": requests,
    }
    store.put("status.json", status)
    total = sum(line["cost"] for d in days.values() for line in d["lines"])
    log.info("stored %d days, %d CE requests, window total %.4f %s", len(days), requests, total, currency)
    return {"days": len(days), "start": start.isoformat(), "endExclusive": end.isoformat(),
            "costExplorerRequests": requests, "windowTotal": round(total, 4), "currency": currency,
            "projectGrouping": grouping["mode"]}


def tag_keys_from_env() -> tuple[str, ...]:
    raw = os.environ.get("PROJECT_TAG_KEYS", "")
    keys = tuple(k.strip() for k in raw.split(",") if k.strip())
    return keys or DEFAULT_TAG_KEYS

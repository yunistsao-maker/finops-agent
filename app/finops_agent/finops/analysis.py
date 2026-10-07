"""Deterministic analysis of the stored cost data.  The agent's tools are thin wrappers around these functions:
the numbers come from here, never from the model.

All periods are inclusive UTC days ``[start, end]``.  Amounts are unblended cost in the account's currency.
"""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from finops.classify import CATEGORIES, CATEGORY_LABELS, classify
from finops.store import Store

DIMENSIONS = ("group", "category", "subcategory", "service", "usage_type", "region", "model", "token_type", "date", "month")
UNALLOCATED = "(unallocated)"
UNTAGGED = "(untagged)"


@dataclass
class Dataset:
    start: date
    end: date
    currency: str = "USD"
    rows: list[dict[str, Any]] = field(default_factory=list)
    days: dict[str, dict[str, Any]] = field(default_factory=dict)  # day -> stored day document
    status: dict[str, Any] | None = None

    @property
    def estimated_days(self) -> list[str]:
        return sorted(d for d, v in self.days.items() if v.get("estimated"))

    @property
    def missing_days(self) -> list[str]:
        out, d = [], self.start
        while d <= self.end:
            if d.isoformat() not in self.days:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return out

    def meta(self) -> dict[str, Any]:
        missing = self.missing_days
        return {
            "period": {"start": self.start.isoformat(), "end": self.end.isoformat(), "days": (self.end - self.start).days + 1},
            "currency": self.currency,
            "dataUpdatedAt": (self.status or {}).get("updatedAt"),
            "estimatedDays": self.estimated_days,
            "missingDays": missing if len(missing) <= 10 else missing[:5] + [f"... {len(missing) - 5} more"],
        }


def _r(x: float) -> float:
    return round(x, 4)


def _row(day: str, line: dict[str, Any]) -> dict[str, Any]:
    c = classify(line.get("service", ""), line.get("usageType", ""))
    return {
        "date": day, "month": day[:7], "service": line.get("service", ""), "usage_type": line.get("usageType", ""),
        "cost": float(line.get("cost") or 0.0), "usage": float(line.get("usage") or 0.0), "unit": line.get("unit", ""),
        "group": c.group, "category": c.category, "subcategory": c.subcategory, "region": c.region,
        "model": c.model, "token_type": c.token_type,
    }


def parse_day(value: str | date | None, name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as e:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}") from e


def resolve_period(store: Store, start: str | date | None = None, end: str | date | None = None,
                   default_days: int = 30) -> tuple[date, date, dict[str, Any] | None]:
    """Fill in a missing end with the last collected day and a missing start with ``default_days`` before it."""
    status = store.status()
    last = parse_day((status or {}).get("coverage", {}).get("lastDay"), "lastDay") or date.today() - timedelta(days=1)
    e = parse_day(end, "end_date") or last
    s = parse_day(start, "start_date") or e - timedelta(days=default_days - 1)
    if s > e:
        raise ValueError(f"start_date {s} is after end_date {e}")
    if (e - s).days > 800:
        raise ValueError("period longer than 800 days")
    return s, e, status


def load(store: Store, start: str | date | None = None, end: str | date | None = None, default_days: int = 30) -> Dataset:
    s, e, status = resolve_period(store, start, end, default_days)
    ds = Dataset(start=s, end=e, status=status, currency=(status or {}).get("currency", "USD"))
    for day, doc, month in store.days(s, e):
        ds.days[day] = doc
        ds.currency = month.get("currency", ds.currency)
        ds.rows.extend(_row(day, line) for line in doc.get("lines", []))
    return ds


def _filter(rows: Iterable[dict[str, Any]], filters: dict[str, str] | None) -> list[dict[str, Any]]:
    if not filters:
        return list(rows)
    for k in filters:
        if k not in DIMENSIONS:
            raise ValueError(f"unknown filter {k!r}; use one of {', '.join(DIMENSIONS)}")
    want = {k: str(v).lower() for k, v in filters.items() if v not in (None, "")}
    return [r for r in rows if all(str(r.get(k) or "").lower() == v for k, v in want.items())]


def _check_dims(group_by: list[str]) -> None:
    bad = [g for g in group_by if g not in DIMENSIONS]
    if bad or not group_by:
        raise ValueError(f"group_by must be one or more of {', '.join(DIMENSIONS)} (got {group_by})")


def summarize(ds: Dataset, group_by: list[str], filters: dict[str, str] | None = None, top_n: int = 25) -> dict[str, Any]:
    _check_dims(group_by)
    rows = _filter(ds.rows, filters)
    total = sum(r["cost"] for r in rows)
    sums: dict[tuple, float] = defaultdict(float)
    for r in rows:
        sums[tuple(r.get(g) or "-" for g in group_by)] += r["cost"]
    ordered = sorted(sums.items(), key=lambda kv: -abs(kv[1]))
    shown = ordered[:max(1, top_n)]
    out_rows = [{**dict(zip(group_by, k)), "cost": _r(v), "share": _r(v / total) if total else 0.0} for k, v in shown]
    if "category" in group_by:
        for r in out_rows:
            r["categoryLabel"] = CATEGORY_LABELS.get(r["category"], r["category"])
    rest = ordered[len(shown):]
    return {**ds.meta(), "groupBy": group_by, "filters": filters or {}, "total": _r(total), "rows": out_rows,
            "notShown": {"count": len(rest), "cost": _r(sum(v for _, v in rest))}}


def trend(ds: Dataset, group_by: str = "group", filters: dict[str, str] | None = None, top_n: int = 6) -> dict[str, Any]:
    _check_dims([group_by])
    rows = _filter(ds.rows, filters)
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[r.get(group_by) or "-"] += r["cost"]
    keep = [k for k, _ in sorted(totals.items(), key=lambda kv: -abs(kv[1]))[:top_n]]
    per_day: dict[str, dict[str, float]] = {d: defaultdict(float) for d in sorted(ds.days)}
    for r in rows:
        k = r.get(group_by) or "-"
        per_day[r["date"]][k if k in keep else "(other)"] += r["cost"]
    days = [{"date": d, "total": _r(sum(v.values())), "by": {k: _r(x) for k, x in v.items() if abs(x) >= 0.00005}}
            for d, v in per_day.items()]
    nonzero = [d["total"] for d in days]
    return {**ds.meta(), "groupBy": group_by, "series": keep, "days": days,
            "dailyAverage": _r(statistics.fmean(nonzero)) if nonzero else 0.0,
            "maxDay": max(days, key=lambda d: d["total"]) if days else None}


def llm_breakdown(ds: Dataset) -> dict[str, Any]:
    rows = [r for r in ds.rows if r["category"] == "llm"]
    total_all = sum(r["cost"] for r in ds.rows)
    llm_total = sum(r["cost"] for r in rows)
    models: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = models.setdefault(r["model"] or "unknown", {"model": r["model"] or "unknown", "cost": 0.0,
                                                         "byTokenType": defaultdict(float), "byRegionalScope": defaultdict(float)})
        m["cost"] += r["cost"]
        m["byTokenType"][r["token_type"] or "other"] += r["cost"]
        m["byRegionalScope"]["global" if "global" in r["usage_type"].lower() else "regional"] += r["cost"]
    out = []
    for m in sorted(models.values(), key=lambda x: -x["cost"]):
        tt = m["byTokenType"]
        cached_input = tt.get("cache_read", 0.0) + tt.get("cache_write", 0.0)
        out.append({
            "model": m["model"], "cost": _r(m["cost"]), "shareOfLlm": _r(m["cost"] / llm_total) if llm_total else 0.0,
            "byTokenType": {k: _r(v) for k, v in sorted(tt.items(), key=lambda kv: -kv[1])},
            "byRegionalScope": {k: _r(v) for k, v in m["byRegionalScope"].items()},
            "inputSideCost": _r(tt.get("input", 0.0) + cached_input), "outputCost": _r(tt.get("output", 0.0)),
        })
    days = max(1, (ds.end - ds.start).days + 1)
    return {**ds.meta(), "llmTotal": _r(llm_total), "accountTotal": _r(total_all),
            "llmShareOfAccount": _r(llm_total / total_all) if total_all else 0.0,
            "llmDailyAverage": _r(llm_total / days), "models": out,
            "note": "Token-type split comes from Bedrock usage types. Per-project LLM split needs a project tag "
                    "on Bedrock application inference profiles (see README)."}


# --------------------------------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------------------------------
def _is_runtime_line(row: dict[str, Any]) -> str | None:
    """'vcpu' / 'memory' for AgentCore Runtime consumption lines, else None."""
    if "AgentCore" not in row["service"] or "Runtime:" not in row["usage_type"]:
        return None
    u = row["usage_type"]
    return "vcpu" if u.endswith("vCPU") else "memory" if u.endswith("Memory") else None


def project_costs(ds: Dataset, include_categories: bool = True) -> dict[str, Any]:
    """Per-project cost.  Per day, the best source available that day:
    tag / cost-category lines when collected, else usage allocation of AgentCore Runtime (rest unallocated)."""
    projects: dict[str, dict[str, Any]] = {}
    modes: dict[str, int] = defaultdict(int)
    rows_by_day: dict[str, list[dict]] = defaultdict(list)
    for r in ds.rows:
        rows_by_day[r["date"]].append(r)

    def add(project: str, category: str, cost: float, how: str) -> None:
        p = projects.setdefault(project, {"project": project, "cost": 0.0, "byCategory": defaultdict(float), "basis": set()})
        p["cost"] += cost
        p["byCategory"][category] += cost
        p["basis"].add(how)

    for day in sorted(ds.days):
        doc = ds.days[day]
        pl = doc.get("projectLines")
        if pl and pl.get("lines") is not None:
            modes[pl.get("source", "tag")] += 1
            for line in pl["lines"]:
                c = classify(line.get("service", ""), "")
                add(line.get("value") or UNTAGGED, c.category, float(line.get("cost") or 0.0), pl.get("source", "tag"))
            continue

        modes["usageAllocation"] += 1
        usage = doc.get("runtimeUsage") or []
        vcpu_total = sum(u.get("vcpuHours", 0.0) for u in usage)
        mem_total = sum(u.get("gbHours", 0.0) for u in usage)
        for r in rows_by_day.get(day, []):
            kind = _is_runtime_line(r)
            denom = vcpu_total if kind == "vcpu" else mem_total if kind == "memory" else 0.0
            if not kind or denom <= 0:
                add(UNALLOCATED, r["category"], r["cost"], "unallocated")
                continue
            field_ = "vcpuHours" if kind == "vcpu" else "gbHours"
            for u in usage:
                share = u.get(field_, 0.0) / denom
                if share:
                    add(u["project"], r["category"], r["cost"] * share, f"agentcoreRuntimeUsage:{u.get('projectSource', 'name')}")

    total = sum(p["cost"] for p in projects.values())
    allocated = sum(p["cost"] for k, p in projects.items() if k not in (UNALLOCATED, UNTAGGED))
    out = []
    for p in sorted(projects.values(), key=lambda x: (x["project"] in (UNALLOCATED, UNTAGGED), -x["cost"])):
        item = {"project": p["project"], "cost": _r(p["cost"]), "share": _r(p["cost"] / total) if total else 0.0,
                "basis": sorted(p["basis"])}
        if include_categories:
            item["byCategory"] = {k: _r(v) for k, v in sorted(p["byCategory"].items(), key=lambda kv: -kv[1]) if abs(v) >= 0.00005}
        out.append(item)
    grouping = (ds.status or {}).get("projectGrouping", {})
    return {**ds.meta(), "daysBySource": dict(modes), "total": _r(total), "allocated": _r(allocated),
            "allocationCoverage": _r(allocated / total) if total else 0.0, "projects": out,
            "groupingStatus": {k: grouping.get(k) for k in ("mode", "key", "activeTagKeys", "checkedTagKeys") if k in grouping}}


# --------------------------------------------------------------------------------------------------
# comparisons, anomalies, projection
# --------------------------------------------------------------------------------------------------
def compare(a: Dataset, b: Dataset, group_by: list[str], top_n: int = 15) -> dict[str, Any]:
    """Period ``a`` (baseline) vs period ``b``.  Daily averages are compared too, so periods may differ in length."""
    _check_dims(group_by)

    def sums(ds: Dataset) -> dict[tuple, float]:
        s: dict[tuple, float] = defaultdict(float)
        for r in ds.rows:
            s[tuple(r.get(g) or "-" for g in group_by)] += r["cost"]
        return s

    sa, sb = sums(a), sums(b)
    da, db = (a.end - a.start).days + 1, (b.end - b.start).days + 1
    rows = []
    for k in set(sa) | set(sb):
        va, vb = sa.get(k, 0.0), sb.get(k, 0.0)
        rows.append({**dict(zip(group_by, k)), "baseline": _r(va), "current": _r(vb), "delta": _r(vb - va),
                     "deltaPct": _r((vb - va) / va) if va else None,
                     "dailyAvgDelta": _r(vb / db - va / da)})
    rows.sort(key=lambda r: -abs(r["dailyAvgDelta"]))
    ta, tb = sum(sa.values()), sum(sb.values())
    return {"baseline": a.meta(), "current": b.meta(), "groupBy": group_by,
            "totals": {"baseline": _r(ta), "current": _r(tb), "delta": _r(tb - ta),
                       "baselineDailyAvg": _r(ta / da), "currentDailyAvg": _r(tb / db)},
            "rows": rows[:top_n], "sortedBy": "absolute change in daily average"}


def anomalies(ds: Dataset, group_by: str = "category", baseline_days: int = 7, min_delta: float = 0.5,
              sigma: float = 2.0) -> dict[str, Any]:
    """Days where a group's cost rose above its trailing mean by more than ``max(sigma * stdev, min_delta)`` and 50%.
    Monthly one-off charges (support, tax) are skipped: they always land on the 1st."""
    _check_dims([group_by])
    days = sorted(ds.days)
    per: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in ds.rows:
        if r["category"] in ("support", "tax"):
            continue
        per[r.get(group_by) or "-"][r["date"]] += r["cost"]
    found = []
    for key, series in per.items():
        values = [series.get(d, 0.0) for d in days]
        for i in range(baseline_days, len(days)):
            window = values[i - baseline_days:i]
            mean = statistics.fmean(window)
            sd = statistics.pstdev(window)
            v = values[i]
            if v - mean > max(sigma * sd, min_delta) and v > 1.5 * mean:
                found.append({"date": days[i], group_by: key, "cost": _r(v), "trailingMean": _r(mean),
                              "excess": _r(v - mean), "ratio": _r(v / mean) if mean else None})
    found.sort(key=lambda x: -x["excess"])
    return {**ds.meta(), "groupBy": group_by, "rule": {"baselineDays": baseline_days, "minDelta": min_delta, "sigma": sigma},
            "anomalies": found[:30], "count": len(found)}


FIXED_MONTHLY = ("support", "tax")


def projection(store: Store, month: str | None = None) -> dict[str, Any]:
    """Month-end estimate from collected data (no Cost Explorer request).

    fixed     support + tax already charged this month (billed once, on the 1st)
    variable  everything else, extrapolated two ways: month-to-date daily average, and the last 7 days' average
    """
    status = store.status() or {}
    last = parse_day(status.get("coverage", {}).get("lastDay"), "lastDay") or date.today() - timedelta(days=1)
    if month:
        y, m = (int(x) for x in month.split("-"))
    else:
        y, m = last.year, last.month
    first = date(y, m, 1)
    ndays = calendar.monthrange(y, m)[1]
    month_end = date(y, m, ndays)
    upto = min(last, month_end)
    if upto < first:
        raise ValueError(f"no collected data for {y:04d}-{m:02d}")
    ds = load(store, first, upto)
    elapsed = (upto - first).days + 1
    fixed = sum(r["cost"] for r in ds.rows if r["category"] in FIXED_MONTHLY)
    variable = sum(r["cost"] for r in ds.rows if r["category"] not in FIXED_MONTHLY)
    recent_start = max(first, upto - timedelta(days=6))
    recent = [r for r in ds.rows if r["category"] not in FIXED_MONTHLY and r["date"] >= recent_start.isoformat()]
    recent_avg = sum(r["cost"] for r in recent) / ((upto - recent_start).days + 1)
    remaining = ndays - elapsed
    by_group: dict[str, float] = defaultdict(float)
    for r in ds.rows:
        by_group[r["group"]] += r["cost"]
    return {**ds.meta(), "month": f"{y:04d}-{m:02d}", "daysElapsed": elapsed, "daysInMonth": ndays,
            "monthToDate": _r(fixed + variable), "monthToDateByGroup": {k: _r(v) for k, v in sorted(by_group.items(), key=lambda kv: -kv[1])},
            "fixedCharged": _r(fixed), "variableMonthToDate": _r(variable),
            "projectedAtMtdRate": _r(fixed + variable + variable / elapsed * remaining),
            "projectedAtLast7DayRate": _r(fixed + variable + recent_avg * remaining),
            "note": "Estimates from collected unblended cost; Cost Explorer's own forecast is available on request."}


def report(store: Store, days: int = 30, end: str | None = None) -> dict[str, Any]:
    """A deterministic overview (no model): what the daily digest and the agent's first answer are built from."""
    ds = load(store, None, end, default_days=days)
    out: dict[str, Any] = {
        "status": {k: (ds.status or {}).get(k) for k in ("updatedAt", "coverage", "projectGrouping", "costExplorerRequests")},
        "byGroup": summarize(ds, ["group"], top_n=10),
        "byCategory": summarize(ds, ["category"], top_n=len(CATEGORIES)),
        "topServices": summarize(ds, ["service"], top_n=10),
        "llm": llm_breakdown(ds),
        "projects": project_costs(ds),
        "anomalies": anomalies(ds),
    }
    try:
        out["projection"] = projection(store)
    except ValueError as e:
        out["projection"] = {"error": str(e)}
    return out

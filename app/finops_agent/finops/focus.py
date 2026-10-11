"""FOCUS ingestion: a cloud provider's FOCUS billing export -> the same month documents the Cost Explorer collector
writes, one store per provider (``finops/v1/<provider>/``).  Azure, Google Cloud, OCI and AWS all emit FOCUS
(FinOps Open Cost and Usage Specification, https://focus.finops.org); ~1.2 is the common version today.

Where the exports come from:

    AWS     Data Exports "FOCUS 1.2 with AWS columns"        -> S3 (or keep the Cost Explorer collector)
    Azure   Cost Management export, dataset "FOCUS"          -> Blob storage
    Google  Cloud Billing FOCUS export (BigQuery), then a scheduled ``EXPORT DATA`` to Cloud Storage
    OCI     FOCUS reports in the usage-report bucket

and are copied into the landing prefix of the data bucket (e.g. by AWS DataSync, which reads Azure Blob and
Cloud Storage).  One export run can be split into several files; they are ingested together, and every day of the
run's billing period(s) that appears in the run replaces the stored day (exports carry month-to-date and providers
revise recent days).  Rows charged *outside* the run's billing periods (corrections to an earlier period, invoiced
in this one) are booked on the first day of the billing period that invoices them, marked ``outOfPeriod``, so
they never overwrite a stored day of another month and re-ingesting a run stays idempotent.

Rows are aggregated to day x service x meter x category x region x sub-account x charge category, which keeps the
documents small (a FOCUS export has one row per resource per hour or day) and matches the Cost Explorer granularity
the analysis already works on.  Classification still happens when data is read (``classify.classify_focus``).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

from finops.store import SCHEMA_VERSION, Store

log = logging.getLogger(__name__)

COST_METRICS = ("BilledCost", "EffectiveCost", "ListCost", "ContractedCost")
# most specific usage text first: FOCUS 1.1+ SkuMeter, provider extensions, then the charge description
METER_COLUMNS = ("SkuMeter", "x_UsageType", "x_SkuMeterName", "x_SkuDescription", "SkuPriceDetails", "ChargeDescription")
PROVIDER_NAME_COLUMNS = ("ServiceProviderName", "ProviderName", "PublisherName")
ESTIMATED_RECENT_DAYS = 3  # providers keep revising the last days of an export


def _first(row: dict[str, Any], columns: Iterable[str]) -> str:
    for c in columns:
        v = row.get(c)
        if v not in (None, ""):
            return str(v)
    return ""


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _day(value: Any) -> str | None:
    """ChargePeriodStart as a UTC calendar day ("2026-10-01T00:00:00Z", "2026-10-01 00:00:00 UTC", date...)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip().replace(" UTC", "Z").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 and text[4] == "-" else None
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).date().isoformat()


def _tags(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, list):  # BigQuery / Parquet map as [{"key": ..., "value": ...}]
        return {str(t.get("key")): str(t.get("value")) for t in value if isinstance(t, dict)}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return _tags(parsed) if isinstance(parsed, (dict, list)) else {}


# ----------------------------------------------------------------------------------------------
# reading export files
# ----------------------------------------------------------------------------------------------
def read_rows(name: str, body: bytes) -> Iterator[dict[str, Any]]:
    """Rows of one export file: CSV, gzip CSV, or Parquet (Parquet needs pyarrow in the deployment package)."""
    lower = name.lower()
    if lower.endswith(".parquet") or lower.endswith(".snappy.parquet"):
        try:
            import pyarrow.parquet as pq  # optional dependency
        except ImportError as e:  # pragma: no cover - depends on the packaging
            raise ValueError(f"{name}: Parquet needs pyarrow; export CSV or add pyarrow to the Lambda") from e
        yield from pq.read_table(io.BytesIO(body)).to_pylist()
        return
    if lower.endswith(".gz"):
        body = gzip.decompress(body)
    text = body.decode("utf-8-sig")
    yield from csv.DictReader(io.StringIO(text))


# ----------------------------------------------------------------------------------------------
# aggregation
# ----------------------------------------------------------------------------------------------
def billing_period_days(rows: list[dict[str, Any]]) -> set[str]:
    """Days of the billing period(s) the run covers (BillingPeriodStart..End); empty when the export lacks them."""
    periods: set[tuple[str, str]] = set()
    for row in rows:
        s, e = _day(row.get("BillingPeriodStart")), _day(row.get("BillingPeriodEnd"))
        if s and e:
            periods.add((s, e))
    days: set[str] = set()
    for s, e in periods:
        d, end = date.fromisoformat(s), date.fromisoformat(e)  # End is exclusive in FOCUS
        while d < end and len(days) < 800:
            days.add(d.isoformat())
            d += timedelta(days=1)
    return days


def aggregate(rows: Iterable[dict[str, Any]], *, cost_metric: str = "BilledCost",
              tag_keys: tuple[str, ...] = ()) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """FOCUS rows -> ({day: {"lines": [...], "projectLines": {...}}}, summary).

    Every row contributes to the project lines (an untagged row with an empty value), so per-project totals add up
    to the account total.
    """
    if cost_metric not in COST_METRICS:
        raise ValueError(f"cost_metric must be one of {', '.join(COST_METRICS)}")
    rows = list(rows)
    periods = billing_period_days(rows)
    lines: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
    units: dict[tuple, str] = {}
    projects: dict[tuple, float] = defaultdict(float)
    out_of_period = 0
    project_key_used: dict[str, int] = defaultdict(int)
    currencies: dict[str, int] = defaultdict(int)
    providers: dict[str, int] = defaultdict(int)
    n = skipped = 0
    for row in rows:
        n += 1
        day = _day(row.get("ChargePeriodStart"))
        if not day:
            skipped += 1
            continue
        booked = day
        if periods and day not in periods:  # a correction to an earlier period: book it where it is invoiced
            booked = _day(row.get("BillingPeriodStart")) or min(periods)
            out_of_period += 1
        cost = _num(row.get(cost_metric))
        usage = _num(row.get("ConsumedQuantity"))
        if row.get("BillingCurrency"):
            currencies[str(row["BillingCurrency"])] += 1
        if (p := _first(row, PROVIDER_NAME_COLUMNS)):
            providers[p] += 1
        service = str(row.get("ServiceName") or "")
        meter = _first(row, METER_COLUMNS)
        charge = str(row.get("ChargeCategory") or "")
        if booked != day:
            charge = (charge + " (out of period)").strip()
        cat, sub = str(row.get("ServiceCategory") or ""), str(row.get("ServiceSubcategory") or "")
        region = str(row.get("RegionId") or row.get("RegionName") or "")
        key = (booked, service, meter, cat, sub, region, str(row.get("SubAccountId") or ""), charge)
        acc = lines[key]
        acc[0] += cost
        acc[1] += usage
        units.setdefault(key, str(row.get("ConsumedUnit") or row.get("PricingUnit") or ""))
        if tag_keys:
            tags = _tags(row.get("Tags"))
            tag = next((k for k in tag_keys if tags.get(k)), None)
            if tag:
                project_key_used[tag] += 1
            # same fields as the line, so a project's cost classifies exactly like the account view
            projects[(booked, tags.get(tag, "") if tag else "", service, meter, cat, sub, region, charge)] += cost

    days: dict[str, dict[str, Any]] = {}
    for key, (cost, usage) in lines.items():
        day, service, meter, cat, sub, region, account, charge = key
        if abs(cost) < 1e-10 and abs(usage) < 1e-10:
            continue
        line = {"service": service, "usageType": meter, "cost": round(cost, 10), "usage": round(usage, 6),
                "unit": units.get(key, ""), "serviceCategory": cat}
        line.update({k: v for k, v in (("serviceSubcategory", sub), ("region", region), ("account", account),
                                       ("chargeCategory", charge)) if v})
        days.setdefault(day, {"lines": []})["lines"].append(line)
    if tag_keys:
        main_key = max(project_key_used, key=project_key_used.get) if project_key_used else tag_keys[0]
        for (day, value, service, meter, cat, sub, region, charge), cost in projects.items():
            if abs(cost) < 1e-10:
                continue
            pl = days.setdefault(day, {"lines": []}).setdefault("projectLines", {"source": "tag", "key": main_key, "lines": []})
            line = {"value": value, "service": service, "usageType": meter, "serviceCategory": cat, "cost": round(cost, 10)}
            line.update({k: v for k, v in (("serviceSubcategory", sub), ("region", region), ("chargeCategory", charge)) if v})
            pl["lines"].append(line)
    for doc in days.values():
        doc["lines"].sort(key=lambda x: -abs(x["cost"]))
    summary = {"rows": n, "skippedRows": skipped, "outOfPeriodRows": out_of_period, "days": len(days),
               "currency": max(currencies, key=currencies.get) if currencies else None,
               "currencies": dict(currencies), "providerNames": dict(providers),
               "projectTagKeysFound": dict(project_key_used)}
    if len(currencies) > 1:
        log.warning("export mixes billing currencies %s; amounts are summed as %s", dict(currencies), summary["currency"])
    return days, summary


# ----------------------------------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------------------------------
def ingest(store: Store, rows: Iterable[dict[str, Any]], *, cost_metric: str = "BilledCost",
           tag_keys: tuple[str, ...] = (), today: date | None = None, source: str = "focus") -> dict[str, Any]:
    """Aggregate one export run and replace the days it contains in ``store``; update ``status.json``."""
    today = today or datetime.now(timezone.utc).date()
    days, summary = aggregate(rows, cost_metric=cost_metric, tag_keys=tag_keys)
    if not days:
        return {**summary, "provider": store.provider, "stored": 0}
    currency = summary["currency"] or (store.status() or {}).get("currency") or "USD"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    estimated_from = (today - timedelta(days=ESTIMATED_RECENT_DAYS)).isoformat()
    by_month: dict[str, list[str]] = defaultdict(list)
    for day in sorted(days):
        by_month[day[:7]].append(day)
    for month, month_days in sorted(by_month.items()):
        doc = store.month(month)
        doc.update(schemaVersion=SCHEMA_VERSION, currency=currency, updatedAt=now, source=source, costMetric=cost_metric)
        for day in month_days:
            doc["days"][day] = {"estimated": day >= estimated_from, "lines": days[day]["lines"],
                                "projectLines": days[day].get("projectLines"), "runtimeUsage": []}
        store.put_month(doc)

    prev = store.status() or {}
    first = min(d for d in (prev.get("coverage", {}).get("firstDay"), min(days)) if d)
    last = max(d for d in (prev.get("coverage", {}).get("lastDay"), max(days)) if d)
    found = summary["projectTagKeysFound"]
    status = {
        "schemaVersion": SCHEMA_VERSION, "provider": store.provider, "source": source, "costMetric": cost_metric,
        "updatedAt": now, "currency": currency,
        "lastWindow": {"start": min(days), "end": max(days)},
        "coverage": {"firstDay": first, "lastDay": last},
        "estimatedDays": sorted(d for d in days if d >= estimated_from),
        "projectGrouping": {"mode": "tag" if found else "none", "key": max(found, key=found.get) if found else None,
                            "checkedTagKeys": list(tag_keys)},
    }
    store.put("status.json", status)
    log.info("%s: stored %d days from %d FOCUS rows (%s)", store.provider, len(days), summary["rows"], cost_metric)
    return {**summary, "provider": store.provider, "stored": len(days), "start": min(days), "end": max(days)}

"""Where collected cost data lives: ``s3://bucket/prefix/`` or a local directory (tests, offline runs).

Layout (schema version 1)::

    <prefix>status.json            last collection: when, coverage, which project grouping is available
    <prefix>months/YYYY-MM.json    one document per month, see ``empty_month``

A month document::

    {"schemaVersion": 1, "month": "2026-10", "accountId": "...", "currency": "USD", "updatedAt": "...",
     "days": {"2026-10-01": {
        "estimated": true,                                    # Cost Explorer may still revise the day
        "lines": [{"service": "...", "usageType": "...", "cost": 1.23, "usage": 4.5, "unit": "..."}],
        "tagLines": {"key": "project", "lines": [{"value": "", "service": "...", "cost": 0.1}]} | null,
        "runtimeUsage": [{"runtime": "...", "arn": "...", "project": "...", "projectSource": "tag|name",
                          "vcpuHours": 0.1, "gbHours": 0.2}]
     }}}
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


def empty_month(month: str) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "month": month, "days": {}}


def months_between(start: date, end_inclusive: date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end_inclusive.year, end_inclusive.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class Store:
    """Read/write JSON documents under one location.  ``uri`` is ``s3://bucket/prefix/`` or a directory path."""

    def __init__(self, uri: str, s3_client: Any = None, provider: str = "aws"):
        self.provider = provider
        if not uri:
            raise ValueError("no data location configured (DATA_URI)")
        self.uri = uri if uri.endswith("/") else uri + "/"
        self._s3 = s3_client
        if self.uri.startswith("s3://"):
            self.bucket, _, self.prefix = self.uri[5:].partition("/")
            if not self.bucket:
                raise ValueError(f"invalid S3 location {uri!r}")
        else:
            self.bucket, self.prefix = None, self.uri

    @classmethod
    def from_env(cls, s3_client: Any = None) -> "Store":
        return cls(os.environ.get("DATA_URI", ""), s3_client)

    @classmethod
    def for_provider(cls, provider: str, s3_client: Any = None) -> "Store":
        """The store one provider's data is written to (see ``provider_uri``)."""
        return cls(provider_uri(provider), s3_client, provider=provider)

    @property
    def s3(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3")
        return self._s3

    def get(self, key: str) -> dict[str, Any] | None:
        if self.bucket is None:
            path = Path(self.prefix) / key
            return json.loads(path.read_text()) if path.exists() else None
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as e:  # AccessDenied on a missing key when the role has no ListBucket
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404", "AccessDenied"):
                return None
            raise
        return json.loads(body)

    def put(self, key: str, doc: dict[str, Any]) -> None:
        body = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
        if self.bucket is None:
            path = Path(self.prefix) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
            return
        self.s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=body, ContentType="application/json")

    # -- typed helpers -------------------------------------------------------------------------
    def month(self, month: str) -> dict[str, Any]:
        doc = self.get(f"months/{month}.json")
        if doc is None:
            return empty_month(month)
        if doc.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError(f"months/{month}.json has schema version {doc.get('schemaVersion')}, expected {SCHEMA_VERSION}")
        return doc

    def put_month(self, doc: dict[str, Any]) -> None:
        self.put(f"months/{doc['month']}.json", doc)

    def status(self) -> dict[str, Any] | None:
        return self.get("status.json")

    @property
    def stores(self) -> list["Store"]:
        return [self]

    def only(self, provider: str) -> "Store":
        if provider.lower() != self.provider:
            raise ValueError(f"no data source for provider {provider!r}; configured: {self.provider}")
        return self

    def days(self, start: date, end_inclusive: date) -> Iterable[tuple[str, dict[str, Any], dict[str, Any]]]:
        """Yield ``(day, dayDoc, monthDoc)`` for every stored day in the range, in order."""
        lo, hi = start.isoformat(), end_inclusive.isoformat()
        for m in months_between(start, end_inclusive):
            doc = self.month(m)
            for d in sorted(doc.get("days", {})):
                if lo <= d <= hi:
                    yield d, doc["days"][d], doc


# ----------------------------------------------------------------------------------------------
# several cloud providers
# ----------------------------------------------------------------------------------------------
PROVIDERS = ("aws", "azure", "gcp", "oci")
STALE_AFTER_DAYS = 3


def provider_uri(provider: str) -> str:
    """``DATA_URI_<PROVIDER>`` when set; otherwise AWS lives at ``DATA_URI`` (backward compatible) and every other
    provider in a sub-prefix of it (``<DATA_URI>azure/``), so one bucket policy covers all of them."""
    explicit = os.environ.get(f"DATA_URI_{provider.upper()}", "")
    if explicit:
        return explicit
    base = os.environ.get("DATA_URI", "")
    if not base:
        raise ValueError("no data location configured (DATA_URI)")
    base = base if base.endswith("/") else base + "/"
    return base if provider == "aws" else f"{base}{provider}/"


def providers_from_env() -> tuple[str, ...]:
    raw = os.environ.get("DATA_PROVIDERS", "aws")
    out = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    bad = [p for p in out if p not in PROVIDERS]
    if bad:
        raise ValueError(f"unknown provider(s) in DATA_PROVIDERS: {bad}; use {', '.join(PROVIDERS)}")
    return out or ("aws",)


class MultiStore:
    """Read-only view over one store per cloud provider (AWS, Azure, Google Cloud...).

    ``status()`` merges the providers' status documents.  The default end of a period is the *earliest* last day
    across providers, so a cross-cloud total never mixes a complete day of one cloud with a missing day of another.
    """

    def __init__(self, stores: list[Store]):
        if not stores:
            raise ValueError("MultiStore needs at least one store")
        self.stores = list(stores)

    @classmethod
    def from_env(cls, s3_client: Any = None) -> "MultiStore":
        return cls([Store.for_provider(p, s3_client) for p in providers_from_env()])

    @property
    def providers(self) -> list[str]:
        return [s.provider for s in self.stores]

    def only(self, provider: str) -> "Store | MultiStore":
        """The store of one provider (a ``provider`` filter reads, converts and checks only that provider)."""
        for s in self.stores:
            if s.provider == provider.lower():
                return s
        raise ValueError(f"no data source for provider {provider!r}; configured: {', '.join(self.providers)}")

    def status(self) -> dict[str, Any] | None:
        per = {s.provider: s.status() for s in self.stores}
        have = {p: st for p, st in per.items() if st}
        if not have:
            return None
        if len(self.stores) == 1:
            return next(iter(have.values()))
        last_by = {p: st.get("coverage", {}).get("lastDay") for p, st in have.items() if st.get("coverage", {}).get("lastDay")}
        first_days = [st.get("coverage", {}).get("firstDay") for st in have.values() if st.get("coverage", {}).get("firstDay")]
        aws = have.get("aws") or {}
        # Default end = earliest last day among providers that are current; a provider more than STALE_AFTER_DAYS
        # behind the newest one is reported as stale instead of freezing everyone's default window.
        newest = max(last_by.values(), default=None)
        stale: dict[str, str] = {}
        if newest:
            cutoff = (date.fromisoformat(newest) - timedelta(days=STALE_AFTER_DAYS)).isoformat()
            stale = {p: d for p, d in last_by.items() if d < cutoff}
        current = [d for p, d in last_by.items() if p not in stale]
        updated = [st.get("updatedAt") for st in have.values() if st.get("updatedAt")]
        missing = sorted(p for p, st in per.items() if not st)
        return {
            "updatedAt": min(updated, default=None),
            "coverage": {"firstDay": min(first_days, default=None), "lastDay": min(current, default=newest),
                         "lastDayByProvider": last_by},
            **({"staleProviders": stale} if stale else {}),
            **({"providersWithoutData": missing} if missing else {}),
            "currency": aws.get("currency") or next((st.get("currency") for st in have.values() if st.get("currency")), "USD"),
            "projectGrouping": aws.get("projectGrouping") or next((st.get("projectGrouping") for st in have.values()
                                                                    if st.get("projectGrouping")), {}),
            "providers": {p: ({k: st.get(k) for k in ("updatedAt", "coverage", "currency", "source", "costMetric")}
                              if st else {"error": "no data collected yet"}) for p, st in per.items()},
        }

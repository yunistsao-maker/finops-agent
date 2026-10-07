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
from datetime import date
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

    def __init__(self, uri: str, s3_client: Any = None):
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

    def days(self, start: date, end_inclusive: date) -> Iterable[tuple[str, dict[str, Any], dict[str, Any]]]:
        """Yield ``(day, dayDoc, monthDoc)`` for every stored day in the range, in order."""
        lo, hi = start.isoformat(), end_inclusive.isoformat()
        for m in months_between(start, end_inclusive):
            doc = self.month(m)
            for d in sorted(doc.get("days", {})):
                if lo <= d <= hi:
                    yield d, doc["days"][d], doc

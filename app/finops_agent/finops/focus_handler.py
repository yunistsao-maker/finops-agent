"""FOCUS ingestion Lambda.

Triggered by S3 when an export run finishes landing in ``<LANDING_PREFIX><provider>/...`` - on the run's
``manifest.json`` (Azure writes it last) or a ``_SUCCESS`` marker (written by the copy job for Google Cloud / OCI) -
or by hand::

    {"provider": "azure", "uri": "s3://bucket/landing/azure/2026-10/<run>/"}

The data files (``.csv``, ``.csv.gz``, ``.parquet``) directly in the run's directory - or exactly the files an Azure
``manifest.json`` lists - are ingested together into ``Store.for_provider(provider)``.  Use one directory per run.

Only the providers in ``FOCUS_PROVIDERS`` are accepted, never ``aws`` (its store belongs to the Cost Explorer
collector).  A run whose manifest lists files that have not arrived yet raises, so the asynchronous invocation is
retried.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import unquote_plus

import boto3

from finops import focus
from finops.collect import tag_keys_from_env
from finops.store import PROVIDERS, Store

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)

DATA_SUFFIXES = (".csv", ".csv.gz", ".parquet", ".snappy.parquet", ".gz")
MARKERS = ("manifest.json", "_SUCCESS")


def _split(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"uri must be s3://bucket/prefix/, got {uri!r}")
    bucket, _, prefix = uri[5:].partition("/")
    return bucket, prefix


class IncompleteRun(RuntimeError):
    """Raised so S3's asynchronous invocation retries once the remaining files have landed."""


def allowed_providers() -> tuple[str, ...]:
    raw = os.environ.get("FOCUS_PROVIDERS", "")
    return tuple(p for p in (x.strip().lower() for x in raw.split(",")) if p in PROVIDERS and p != "aws")


def _check(provider: str) -> str:
    provider = provider.lower()
    allowed = allowed_providers()
    if provider not in allowed:
        raise ValueError(f"provider {provider!r} is not ingested here (FOCUS_PROVIDERS={','.join(allowed) or 'none'}); "
                         "AWS data comes from the Cost Explorer collector")
    return provider


def _provider_of(key: str) -> str:
    """landing/<provider>/... -> provider."""
    landing = os.environ.get("LANDING_PREFIX", "landing/")
    rest = key[len(landing):] if key.startswith(landing) else key
    provider = rest.split("/", 1)[0].lower()
    if provider not in PROVIDERS:
        raise ValueError(f"cannot tell the provider from {key!r}; expected {landing}<{'|'.join(PROVIDERS)}>/...")
    return _check(provider)


def _runs(event: dict) -> list[tuple[str, str, str]]:
    """(provider, bucket, directory prefix) per export run in the event."""
    if event.get("uri"):
        bucket, prefix = _split(event["uri"])
        provider = _check(event["provider"]) if event.get("provider") else _provider_of(prefix)
        return [(provider, bucket, prefix if prefix.endswith("/") else prefix + "/")]
    out = []
    for rec in event.get("Records", []):
        key = unquote_plus(rec["s3"]["object"]["key"])
        if not key.endswith(MARKERS):
            continue
        out.append((_provider_of(key), rec["s3"]["bucket"]["name"], key.rsplit("/", 1)[0] + "/"))
    return out


def _manifest(s3, bucket: str, prefix: str) -> tuple[int | None, list[str]]:
    """(file count, file base names) an Azure export ``manifest.json`` lists; (None, []) without a manifest."""
    try:
        body = s3.get_object(Bucket=bucket, Key=prefix + "manifest.json")["Body"].read()
    except Exception:  # noqa: BLE001 - no manifest (Google Cloud / OCI runs use _SUCCESS or a manual trigger)
        return None, []
    try:
        doc = json.loads(body)
    except ValueError:
        return None, []
    blobs = doc.get("blobs") if isinstance(doc.get("blobs"), list) else []
    names = [str(b.get("blobName", "")).rsplit("/", 1)[-1] for b in blobs if isinstance(b, dict) and b.get("blobName")]
    count = doc["blobCount"] if isinstance(doc.get("blobCount"), int) else (len(blobs) if blobs else None)
    return count, names


def _run_files(s3, bucket: str, prefix: str) -> list[str]:
    """Data files directly in the run directory (not in sub-directories: those are other runs)."""
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].lower().endswith(DATA_SUFFIXES)]
    return keys


def handler(event, context):
    event = event if isinstance(event, dict) else {}
    s3 = boto3.client("s3")
    metric = os.environ.get("COST_METRIC", "BilledCost")
    results = []
    for provider, bucket, prefix in _runs(event):
        keys = _run_files(s3, bucket, prefix)
        expected, listed = _manifest(s3, bucket, prefix)
        if listed:  # exactly the run's files (a re-used directory may hold leftovers of an earlier run)
            keys = [k for k in keys if k.rsplit("/", 1)[-1] in set(listed)]
        if expected is not None and len(keys) < expected:
            # a copy job may land the manifest before the parts: fail so the async invocation is retried
            raise IncompleteRun(f"s3://{bucket}/{prefix}: {len(keys)} of {expected} files present")
        if not keys:
            results.append({"provider": provider, "prefix": prefix, "error": "no data files"})
            continue

        def rows():
            for key in sorted(keys):
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                yield from focus.read_rows(key, body)

        store = Store.for_provider(provider, s3)
        out = focus.ingest(store, rows(), cost_metric=metric, tag_keys=tag_keys_from_env())
        log.info("ingested %s files from s3://%s/%s: %s", len(keys), bucket, prefix, out)
        results.append({**out, "files": len(keys), "prefix": prefix})
    return {"runs": results}

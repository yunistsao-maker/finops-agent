"""Collector Lambda: run daily by EventBridge, or by hand for a backfill.

Event (all optional)::

    {"backfillDays": 90}                              re-read the last 90 complete days
    {"start": "2026-07-01", "end": "2026-10-01"}       re-read [start, end)
"""

from __future__ import annotations

import logging
import os
from datetime import date

import boto3

from finops import collect
from finops.store import Store

logging.getLogger().setLevel(logging.INFO)


def _date(value: object, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}") from e


def handler(event, context):
    event = event if isinstance(event, dict) else {}
    lookback = int(event.get("backfillDays") or os.environ.get("LOOKBACK_DAYS") or 7)
    region = os.environ.get("CE_REGION", "us-east-1")  # Cost Explorer has a single endpoint
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    return collect.run(
        Store.from_env(),
        ce=boto3.client("ce", region_name=region),
        cw=boto3.client("cloudwatch"),
        tagging=boto3.client("resourcegroupstaggingapi"),
        account_id=account_id,
        lookback_days=lookback,
        start=_date(event.get("start"), "start"),
        end=_date(event.get("end"), "end"),
        tag_keys=collect.tag_keys_from_env(),
        cost_category=os.environ.get("COST_CATEGORY", ""),
    )

#!/usr/bin/env python3
"""Write a synthetic cost dataset to a local directory, so the agent and the web UI can run without AWS cost data.

    python3 scripts/seed_local_data.py .local-data                 # AWS only (Cost Explorer format)
    python3 scripts/seed_local_data.py .local-data --multicloud    # + Azure and Google Cloud FOCUS data

Then run the agent against it:  DATA_URI=$PWD/.local-data/ [DATA_PROVIDERS=aws,azure,gcp] python main.py
The numbers are made up (fixed seed); a BigQuery spike and an LLM spike are planted for anomaly detection.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "finops_agent"))

from finops import collect, focus  # noqa: E402
from finops.store import Store  # noqa: E402


def ce_group(service: str, usage_type: str, cost: float) -> dict:
    return {"Keys": [service, usage_type],
            "Metrics": {"UnblendedCost": {"Amount": str(cost), "Unit": "USD"},
                        "UsageQuantity": {"Amount": "1", "Unit": "Units"}}}


class FakeCostExplorer:
    """Answers the collector's GetCostAndUsage / GetTags calls from a {day: [group, ...]} dict."""

    def __init__(self, days: dict[str, list[dict]]):
        self.days = days

    def get_cost_and_usage(self, **kw):
        start, end = date.fromisoformat(kw["TimePeriod"]["Start"]), date.fromisoformat(kw["TimePeriod"]["End"])
        out, d = [], start
        while d < end:
            out.append({"TimePeriod": {"Start": d.isoformat()}, "Groups": self.days.get(d.isoformat(), []),
                        "Estimated": d >= end - timedelta(days=2)})
            d += timedelta(days=1)
        return {"ResultsByTime": out}

    def get_tags(self, **kw):
        return {"Tags": []}


def focus_row(day: str, service: str, meter: str, cost: float, category: str, sub: str = "", region: str = "",
              account: str = "", project: str = "") -> dict:
    month = date.fromisoformat(day).replace(day=1)
    nxt = (month + timedelta(days=32)).replace(day=1)
    return {"BillingPeriodStart": f"{month}T00:00:00Z", "BillingPeriodEnd": f"{nxt}T00:00:00Z",
            "ChargePeriodStart": f"{day}T00:00:00Z", "BilledCost": cost, "EffectiveCost": cost, "BillingCurrency": "USD",
            "ServiceName": service, "ServiceCategory": category, "ServiceSubcategory": sub, "SkuMeter": meter,
            "RegionId": region, "SubAccountId": account, "ChargeCategory": "Usage", "ConsumedQuantity": 1,
            "ConsumedUnit": "Units", "Tags": json.dumps({"project": project} if project else {})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--multicloud", action="store_true", help="also write Azure and Google Cloud FOCUS data")
    args = ap.parse_args()

    rnd = random.Random(42)
    root = Path(args.directory).resolve()
    today = date.today()
    first = today - timedelta(days=args.days)
    days = [(first + timedelta(days=i)).isoformat() for i in range(args.days)]

    aws: dict[str, list[dict]] = {}
    for i, d in enumerate(days):
        spike = 4.0 if i == args.days - 9 else 1.0
        aws[d] = [
            ce_group("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_input_tokens_standard-Units", round(rnd.uniform(1.5, 3) * spike, 4)),
            ce_group("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_output_tokens_standard-Units", round(rnd.uniform(1, 2.5), 4)),
            ce_group("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_cache_read_input_token_count-Units", round(rnd.uniform(0.1, 0.4), 4)),
            ce_group("Claude Haiku 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_input_tokens_standard-Units", round(rnd.uniform(0.1, 0.4), 4)),
            ce_group("Amazon Bedrock AgentCore", "USE1-Runtime:vCPU", round(rnd.uniform(0.2, 0.6), 4)),
            ce_group("Amazon Bedrock AgentCore", "USE1-Runtime:Memory", round(rnd.uniform(0.05, 0.2), 4)),
            ce_group("EC2 - Other", "USE1-NatGateway-Hours", 1.08),
            ce_group("Amazon Simple Storage Service", "USE1-TimedStorage-ByteHrs", 0.05),
            ce_group("AmazonCloudWatch", "USE1-CW:MetricMonitorUsage", 0.12),
        ] + ([ce_group("AWS Business Support+", "NoUsageType", 29.0)] if d.endswith("-01") else [])
    out = collect.run(Store(str(root)), ce=FakeCostExplorer(aws), today=today, lookback_days=args.days)
    print(f"aws:   {out['days']} days, total {out['windowTotal']} USD -> {root}/")

    if args.multicloud:
        az, gc = [], []
        for i, d in enumerate(days[:-1]):  # FOCUS exports usually lag a day
            az += [
                focus_row(d, "Azure OpenAI", "gpt-4.1-Inp-glbl Tokens", round(rnd.uniform(1, 3), 4), "AI and Machine Learning", "Generative AI", "eastus", "sub-prod", "copilot"),
                focus_row(d, "Azure OpenAI", "gpt-4.1-Outp-glbl Tokens", round(rnd.uniform(1, 2), 4), "AI and Machine Learning", "Generative AI", "eastus", "sub-prod", "copilot"),
                focus_row(d, "Azure OpenAI", "gpt-4.1 cached Inp glbl Tokens", round(rnd.uniform(0.1, 0.3), 4), "AI and Machine Learning", "Generative AI", "eastus", "sub-prod", "copilot"),
                focus_row(d, "Virtual Machines", "D4s v5", 2.3, "Compute", region="eastus", account="sub-prod", project="copilot"),
                focus_row(d, "Bandwidth", "Standard Data Transfer Out", 0.4, "Networking", region="eastus", account="sub-prod"),
            ]
            gc += [
                focus_row(d, "Vertex AI", "Gemini 2.5 Pro Input Text Tokens", round(rnd.uniform(0.5, 1.5), 4), "AI and Machine Learning", region="asia-east1", account="proj-search", project="search"),
                focus_row(d, "Vertex AI", "Gemini 2.5 Pro Output Text Tokens", round(rnd.uniform(0.3, 1.0), 4), "AI and Machine Learning", region="asia-east1", account="proj-search", project="search"),
                focus_row(d, "Cloud Run", "CPU Allocation Time", 0.6, "Compute", region="asia-east1", account="proj-search", project="search"),
                focus_row(d, "BigQuery", "Analysis", 6.0 if i == args.days - 12 else 0.3, "Analytics", region="asia-east1", account="proj-search"),
            ]
        for provider, rows in (("azure", az), ("gcp", gc)):
            res = focus.ingest(Store(str(root / provider), provider=provider), rows, tag_keys=("project",), today=today)
            print(f"{provider}: {res['stored']} days from {res['rows']} FOCUS rows -> {root / provider}/")

    providers = "aws,azure,gcp" if args.multicloud else "aws"
    print(f"\nrun the agent:\n  cd app/finops_agent && DATA_URI={root}/ DATA_PROVIDERS={providers} python main.py")


if __name__ == "__main__":
    main()

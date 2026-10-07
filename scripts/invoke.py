#!/usr/bin/env python3
"""Ask the deployed FinOps agent a question and stream the answer.

    python3 scripts/invoke.py "What did LLMs cost last week?"
    python3 scripts/invoke.py --report 30            # deterministic JSON report, no model call
    python3 scripts/invoke.py --session <id> "..."   # continue a conversation (id >= 33 chars)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import boto3
from botocore.config import Config

RUNTIME_NAME_PREFIX = "FinopsAgent_finops_agent"


def runtime_arn(region: str) -> str:
    if os.environ.get("FINOPS_RUNTIME_ARN"):
        return os.environ["FINOPS_RUNTIME_ARN"]
    ctl = boto3.client("bedrock-agentcore-control", region_name=region)
    token = None
    while True:
        resp = ctl.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for rt in resp.get("agentRuntimes", []):
            if rt["agentRuntimeName"] == RUNTIME_NAME_PREFIX or rt["agentRuntimeName"].startswith(RUNTIME_NAME_PREFIX):
                return rt["agentRuntimeArn"]
        token = resp.get("nextToken")
        if not token:
            sys.exit(f"no runtime named {RUNTIME_NAME_PREFIX}* in {region}; deploy first or set FINOPS_RUNTIME_ARN")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--report", type=int, metavar="DAYS")
    ap.add_argument("--session", default=None)
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = ap.parse_args()
    if not args.prompt and not args.report:
        ap.error("give a prompt or --report DAYS")

    payload = {"action": "report", "days": args.report} if args.report else {"prompt": args.prompt}
    session = args.session or f"finops-{uuid.uuid4()}"
    client = boto3.client("bedrock-agentcore", region_name=args.region, config=Config(read_timeout=600))
    resp = client.invoke_agent_runtime(agentRuntimeArn=runtime_arn(args.region), runtimeSessionId=session,
                                       payload=json.dumps(payload).encode())
    print(f"[session {session}]", file=sys.stderr)
    body = resp["response"]
    if "text/event-stream" not in resp.get("contentType", ""):
        print(body.read().decode())
        return
    for raw in body.iter_lines():
        line = raw.decode("utf-8", "replace")
        if not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        if isinstance(data, str):
            print(data, end="", flush=True)
        else:
            print(json.dumps(data, indent=1, ensure_ascii=False))
    print()


if __name__ == "__main__":
    main()

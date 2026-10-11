"""AgentCore Runtime entrypoint of the FinOps agent.

Payload:
    {"prompt": "What did LLMs cost us last week?"}     answer with the model, streamed
    {"prompt": "...", "events": true}                  same, plus tool calls and their JSON results (for the web UI)
    {"action": "report", "days": 30, "end": "YYYY-MM-DD"}   deterministic JSON overview, no model call
    {"action": "status"}                               configured clouds, data freshness, tools (no model call)

The agent only reads the cost data the collector / FOCUS ingestion write to DATA_URI (one prefix per cloud provider,
see finops.store.MultiStore); numbers come from finops.analysis.

Event contract for the web UI (events=true): text deltas are JSON strings; tool traffic is
    {"type": "tool_call", "id", "name", "input"}
    {"type": "tool_result", "id", "name", "input", "status": "success" | "error", "result": <JSON object>}
where a failed call always has result {"error": "..."} - never free text the UI would have to guess about.
"""

import json
from collections import OrderedDict
from datetime import datetime, timezone

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

from finops import analysis
from finops.store import MultiStore
from finops.tools import StrictToolArgs, build_tools
from model.load import load_model

app = BedrockAgentCoreApp()
log = app.logger

SYSTEM_PROMPT = """You are a FinOps analyst for {scope}. Today is {today} (UTC).

Answer only from your tools: they read daily cost data collected into S3 ({sources}). Never estimate
or invent a number a tool did not return, and never add up numbers yourself when a tool can return the total.
Call data_status first in a conversation to know the covered days per provider.

Cost taxonomy (group -> categories):
- llm: Bedrock model inference, per model and token type (input, output, cache_read, cache_write)
- infrastructure: ai_agent_platform (AgentCore runtime/memory/gateway/tools, KB, guardrails), compute, storage, database
- networking: data transfer, NAT, public IPv4, endpoints, load balancers, DNS, CDN - from any service
- platform_services: observability, security_and_management, analytics, app_integration
- support_and_tax: AWS Support plan and tax (monthly charges billed on the 1st)

Multi-cloud: every tool takes a provider filter (aws | azure | gcp | oci) and "provider" is a dimension, so
cross-cloud totals come from one query over normalized data. LLM cost covers Bedrock, Azure OpenAI / Foundry and
Vertex AI token meters alike. If the tools report converted currencies (fx), say so.

Charts: the user's screen renders a chart for every tool result. When the user asks to plot, chart, graph,
visualize or "show" cost, call plot_cost (x=date|month for time, any other dimension for bars; series to split).
Never draw charts as text, ASCII, code or markdown images. Only use argument names a tool declares.

How to answer:
- State the period (inclusive dates) and currency. Mention when days are still "estimated".
- Lead with the answer, then a compact table. Use 2 decimals, or 4 when amounts are below 1.
- For projects, say which basis was used (tag, cost category, or AgentCore usage allocation) and the
  allocationCoverage; unallocated cost is not an error, say what would allocate it.
- When it helps, end with at most 3 concrete optimization suggestions grounded in the data (e.g. prompt-cache
  hit ratio, model choice, idle runtimes, data transfer).
- Only call month_end_projection with use_cost_explorer_forecast=true when the user asks for AWS's forecast.
"""

_SESSIONS: "OrderedDict[str, Agent]" = OrderedDict()
MAX_SESSIONS = 64


def _agent(session_id: str) -> Agent:
    """One Agent per runtime session keeps a conversation's history (in process; lost on a cold start)."""
    if session_id in _SESSIONS:
        _SESSIONS.move_to_end(session_id)
        return _SESSIONS[session_id]
    if len(_SESSIONS) >= MAX_SESSIONS:
        _SESSIONS.popitem(last=False)
    today = datetime.now(timezone.utc).date().isoformat()
    store = MultiStore.from_env()
    providers = store.providers
    names = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud", "oci": "Oracle Cloud"}
    scope = ("one AWS account" if providers == ["aws"]
             else "a multi-cloud estate: " + ", ".join(names.get(p, p) for p in providers))
    sources = ", ".join({"aws": "AWS Cost Explorer, unblended"}.get(p, f"{names.get(p, p)} FOCUS export")
                        for p in providers)
    _SESSIONS[session_id] = Agent(
        model=load_model(),
        system_prompt=SYSTEM_PROMPT.format(today=today, scope=scope, sources=sources),
        tools=build_tools(store),
        hooks=[StrictToolArgs()],
        conversation_manager=SlidingWindowConversationManager(window_size=40, should_truncate_results=True),
        callback_handler=None,
    )
    return _SESSIONS[session_id]


@app.entrypoint
async def invoke(payload, context):
    if not isinstance(payload, dict):
        yield {"error": "payload must be a JSON object"}
        return

    if payload.get("action") == "report":
        try:
            days = int(payload.get("days") or 30)
            if not 1 <= days <= 400:
                raise ValueError("days must be between 1 and 400")
            yield analysis.report(MultiStore.from_env(), days=days, end=payload.get("end"))
        except ValueError as e:
            yield {"error": str(e)}
        return

    if payload.get("action") == "status":
        try:
            yield system_status(MultiStore.from_env())
        except ValueError as e:
            yield {"error": str(e)}
        return

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        yield {"error": 'send {"prompt": "<question>"} or {"action": "report"}'}
        return
    if len(prompt) > 8000:
        yield {"error": "prompt longer than 8000 characters"}
        return

    session_id = getattr(context, "session_id", None) or "default-session"
    with_events = payload.get("events") is True
    log.info("question session=%s chars=%d events=%s", session_id, len(prompt), with_events)
    calls: dict[str, dict] = {}
    async for event in _agent(session_id).stream_async(prompt):
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("data"), str):
            yield event["data"]  # text deltas; without "events" tool traffic stays inside the runtime
        elif with_events and isinstance(event.get("message"), dict):
            for out in _tool_events(event["message"], calls):
                yield out


def system_status(store: MultiStore) -> dict:
    """What the "How it works" page shows: configured clouds, per-provider freshness, tools and model.
    Reads status documents only - no model call, no Cost Explorer request."""
    import os

    from finops.collect import tag_keys_from_env
    from finops.store import STALE_AFTER_DAYS

    status = store.status() or {}
    per = {s.provider: (s.status() or {}) for s in store.stores}
    return {
        "providers": [{
            "provider": p,
            "source": st.get("source") or ("costExplorer" if p == "aws" and st else None),
            "costMetric": st.get("costMetric") or ("UnblendedCost" if p == "aws" and st else None),
            "currency": st.get("currency"),
            "updatedAt": st.get("updatedAt"),
            "coverage": st.get("coverage"),
            "estimatedDays": len(st.get("estimatedDays") or []),
            "projectGrouping": (st.get("projectGrouping") or {}).get("mode"),
            "stale": p in (status.get("staleProviders") or {}),
            "hasData": bool(st),
        } for p, st in per.items()],
        "coverage": status.get("coverage"),
        "staleAfterDays": STALE_AFTER_DAYS,
        "reportingCurrency": os.environ.get("REPORTING_CURRENCY") or None,
        "projectTagKeys": list(tag_keys_from_env()),
        "model": os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5-5"),
        "tools": [t.tool_name for t in build_tools(store)],
    }


def _tool_events(message: dict, calls: dict[str, dict]):
    """Tool calls (assistant message) and their results (user message) as typed events, so the UI can chart the
    numbers the tools computed instead of parsing the model's prose."""
    for block in message.get("content") or []:
        if "toolUse" in block:
            use = block["toolUse"]
            calls[use["toolUseId"]] = {"name": use.get("name"), "input": use.get("input") or {}}
            yield {"type": "tool_call", "id": use["toolUseId"], **calls[use["toolUseId"]]}
        elif "toolResult" in block:
            res = block["toolResult"]
            call = calls.pop(res.get("toolUseId"), {})
            status = res.get("status") or "success"
            result = _result_json(res.get("content") or [], status)
            if "error" in result:
                status = "error"
            yield {"type": "tool_result", "id": res.get("toolUseId"), "status": status,
                   "name": call.get("name"), "input": call.get("input", {}), "result": result}


def _result_json(content: list, status: str) -> dict:
    """A tool result as one JSON object.  Strands reports argument-validation failures, cancelled calls and
    exceptions as plain text ("Error: Validation failed ...") with status "error"; turn those into {"error": ...}
    so the UI never receives a shape it would try to chart."""
    texts = [c["text"] for c in content if "text" in c]
    text = "\n".join(texts)
    try:
        value = json.loads(text) if len(texts) == 1 else None
    except ValueError:
        value = None
    if isinstance(value, dict):
        result = value
    elif value is not None:
        result = {"value": value}
    else:
        result = {"text": text}
    if status == "error" and "error" not in result:
        msg = text.strip()
        result = {"error": (msg[len("Error:"):].strip() if msg.startswith("Error:") else msg) or "tool call failed"}
    return result


if __name__ == "__main__":
    app.run()

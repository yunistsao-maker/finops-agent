"""AgentCore Runtime entrypoint of the FinOps agent.

Payload:
    {"prompt": "What did LLMs cost us last week?"}     answer with the model, streamed
    {"action": "report", "days": 30, "end": "YYYY-MM-DD"}   deterministic JSON overview, no model call

The agent only reads the cost data the collector Lambda writes to DATA_URI; numbers come from finops.analysis.
"""

from collections import OrderedDict
from datetime import datetime, timezone

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

from finops import analysis
from finops.store import Store
from finops.tools import build_tools
from model.load import load_model

app = BedrockAgentCoreApp()
log = app.logger

SYSTEM_PROMPT = """You are a FinOps analyst for one AWS account. Today is {today} (UTC).

Answer only from your tools: they read daily Cost Explorer data (unblended cost) collected into S3. Never estimate
or invent a number a tool did not return. Call data_status first in a conversation to know the covered days.

Cost taxonomy (group -> categories):
- llm: Bedrock model inference, per model and token type (input, output, cache_read, cache_write)
- infrastructure: ai_agent_platform (AgentCore runtime/memory/gateway/tools, KB, guardrails), compute, storage, database
- networking: data transfer, NAT, public IPv4, endpoints, load balancers, DNS, CDN - from any service
- platform_services: observability, security_and_management, analytics, app_integration
- support_and_tax: AWS Support plan and tax (monthly charges billed on the 1st)

How to answer:
- State the period (inclusive dates) and currency. Mention when days are still "estimated" by Cost Explorer.
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
    _SESSIONS[session_id] = Agent(
        model=load_model(),
        system_prompt=SYSTEM_PROMPT.format(today=today),
        tools=build_tools(),
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
            yield analysis.report(Store.from_env(), days=days, end=payload.get("end"))
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
    log.info("question session=%s chars=%d", session_id, len(prompt))
    async for event in _agent(session_id).stream_async(prompt):
        if isinstance(event, dict) and "data" in event and isinstance(event["data"], str):
            yield event["data"]  # text deltas only: tool traffic stays inside the runtime


if __name__ == "__main__":
    app.run()

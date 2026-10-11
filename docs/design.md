# Design notes

Why the FinOps agent is built the way it is. Setup and operations are in the [README](../README.md).

1. [Principle: numbers come from code](#1-principle-numbers-come-from-code)
2. [How the agent is controlled](#2-how-the-agent-is-controlled)
3. [FOCUS: the common billing standard](#3-focus-the-common-billing-standard)
4. [Pooled exports, live APIs and skills](#4-pooled-exports-live-apis-and-skills)
5. [Next steps](#5-next-steps)

## 1. Principle: numbers come from code

The model chooses tools and explains results; it never computes, merges or converts amounts. Every number a user
sees (in the answer or in a chart) comes from `finops/analysis.py` over normalized data. This makes answers
reproducible, testable (`tests/`), and auditable: the UI charts the tool results, not the model's prose.

## 2. How the agent is controlled

Four layers, from soft to hard. The harder the layer, the more it is trusted for correctness.

| Layer | Where | What it decides |
| --- | --- | --- |
| System prompt | `main.py` `SYSTEM_PROMPT` | Behaviour: answer only from tools, call `data_status` first, state period/currency/estimated days, lead with the answer, at most 3 grounded suggestions, use `plot_cost` for any chart, paid Cost Explorer forecast only on request. Built per deployment from `DATA_PROVIDERS`. |
| Tool design | `finops/tools.py` | What analyses exist. Tools are *analysis actions* (`compare_periods`, `find_anomalies`, `month_end_projection`, `llm_costs`, `plot_cost`, ...) rather than raw data access, and their docstrings are the tool specs the model reads. No free-form SQL tool: the model picks an action and parameters, not a method. |
| Deterministic analysis | `finops/analysis.py`, `finops/classify.py` | How cost is analysed: taxonomy (applied when data is read, so rule changes re-classify history), anomaly rule (trailing 7-day mean, > 2 sigma, > +50 %, >= `min_delta`), projections, fixed monthly charges, project allocation, currency handling. |
| Guardrails | `tools.StrictToolArgs`, `tools._guard`, `main._result_json`, web `ToolResultView` | Undeclared tool arguments are rejected (Strands would drop them silently); every failure is a JSON `{"error"}` the model can correct; the UI only charts results that pass a shape check, each inside an error boundary; the runtime only reads S3; paid Cost Explorer calls are capped per session (`MAX_LIVE_CE_CALLS`). |

There are no skills today: every analysis is a single deterministic step. Skills (or prompt playbooks) become useful for
**multi-step investigations** - e.g. anomaly root cause (find the spike -> split by usage type -> compare periods ->
CloudTrail), prompt-cache health checks, month-end showback - and for provider knowledge (section 4). Loaded on demand,
they keep ordinary questions free of long instructions. Every number inside a playbook still comes from a tool.

## 3. FOCUS: the common billing standard

**FOCUS** (FinOps Open Cost and Usage Specification, https://focus.finops.org) is an open specification from the FinOps
Foundation (Linux Foundation) for cloud billing data. A **FOCUS export** is a provider's billing detail delivered in
that format. The provider does the mapping; consumers get the same columns with the same meaning from every cloud.

Without it, each cloud names and defines things differently:

| | AWS (CUR) | Azure (cost details) | Google Cloud (BigQuery export) | FOCUS |
| --- | --- | --- | --- | --- |
| Amount | `line_item_unblended_cost` | `costInBillingCurrency` | `cost` | `BilledCost` |
| Amortized amount | separate columns | separate *Amortized* dataset | derived from credits | `EffectiveCost` |
| Service | `product_servicecode` | `meterCategory` | `service.description` | `ServiceName`, `ServiceCategory` |
| Usage detail | `line_item_usage_type` | `meterName` | `sku.description` | `SkuMeter` |
| Sub-account | `line_item_usage_account_id` | `SubscriptionId` | `project.id` | `SubAccountId` |

Columns this agent relies on:

| Column | Meaning |
| --- | --- |
| `BilledCost` | What is invoiced (comparable to AWS unblended cost). Default `COST_METRIC`. |
| `EffectiveCost` | Cost after amortizing commitments (RIs, Savings Plans, CUDs); best for showback. |
| `ServiceCategory` / `ServiceSubcategory` | Standard service classes (Compute, Storage, Networking, AI and Machine Learning ...). |
| `ChargeCategory` | Usage, Purchase, Tax, Credit, Adjustment. |
| `BillingPeriodStart/End`, `ChargePeriodStart/End` | Which invoice period a charge belongs to, and when it was incurred (used to book corrections). |
| `BillingCurrency` | Currency of the amounts (mixed currencies require `REPORTING_CURRENCY` + `FX_RATES`). |
| `Tags` | Normalized resource tags: cross-cloud project allocation (`PROJECT_TAG_KEYS`). |
| `x_*` | Provider extensions, e.g. AWS `x_UsageType` (lets AWS FOCUS lines use the AWS classifier). |

Where the exports come from (status as researched in October 2026):

| Provider | Configure | Delivered to | Status |
| --- | --- | --- | --- |
| AWS | Billing -> Data Exports, "FOCUS 1.2 with AWS columns" | S3 (Parquet / CSV) | GA |
| Azure | Cost Management -> Exports, "Cost and usage details (FOCUS)" | Storage account, with `manifest.json` | schema index lists 1.2-preview |
| Google Cloud | Cloud Billing BigQuery export, FOCUS option | BigQuery table | preview (since June 2026) |

Exports refresh daily with month-to-date detail; recent days are revised, with roughly a day of latency.

Limits to keep in mind:

- The specification is at 1.4 (June 2026), but providers emit about **1.2** - that is the practical common baseline.
- **No standard LLM token columns yet** (planned for 1.5). `classify.classify_focus` parses model and token type from
  `SkuMeter` text (e.g. `gpt-4.1-Inp-glbl Tokens` -> model `gpt-4.1`, `input`).
- Providers still deviate in details (Google documents conformance gaps); keep the native export as a fallback.
- FOCUS describes **what was spent**, not **what to change**: rightsizing, idle resources and commitment advice need each
  provider's recommendation APIs.

## 4. Pooled exports, live APIs and skills

FinOps needs one standard across clouds. The question is not "data pool or skills" but **where the normalization runs**:

- Skills are instructions (optionally with scripts). If a skill only *tells* the model how to map Azure or Google
  fields and add them up, the model does the normalization and arithmetic: not reproducible, not auditable.
- If a skill's script calls the provider API and normalizes in code, the code is the standard - the same work as
  ingestion, run at query time instead of ahead of time.

Either way the standard must be code. Querying provider APIs live for every question has concrete problems:

1. **Google Cloud has no spend API.** The Cloud Billing API (and its MCP server) covers accounts, SKUs and prices;
   spend is only in the BigQuery billing export - a data pool already.
2. **API semantics differ.** Cost Explorer: unblended / amortized / net amortized, at most 2 group-by dimensions per
   request, USD 0.01 per paginated request. Azure Cost Management Query: actual vs amortized, scopes
   (subscription / management group / billing account), throttling, billing-account currency. Plus time zones, day
   boundaries, late revisions, credits and commitment amortization - the mapping FOCUS exports already provide.
3. **History is needed** for trends, anomalies and projections; re-fetching 90 days x dimensions per question is slow,
   paid and throttled - and caching it is a pool again.
4. **Not reproducible.** Live numbers change as providers revise them; finance needs "this number came from that
   snapshot".
5. **Larger blast radius and context.** The runtime would hold read credentials for every cloud at all times, and raw
   API responses are large.

Design: let each part do what it is good at.

```
                 shared standard (code, never in a skill)
                 FOCUS schema + taxonomy (classify.py) + analysis.py
           ^ history / estate-wide (pooled)            ^ fresh / drill-down (live)
   FOCUS exports -> S3 stores (today)          live adapters (next): Cost Explorer, Azure Cost
   trends, anomalies, showback, month end      Management Query, BigQuery -> the same FOCUS-shaped lines

   skills (next), one per provider - judgement, not arithmetic:
     aws-finops    Savings Plans / RIs, Cost Optimization Hub, CUR/FOCUS x_ columns, anomaly playbook
     azure-finops  reservations vs savings plan, meter names, Advisor, choosing a scope
     gcp-finops    CUDs, Recommender, querying the BigQuery export
```

- `store.MultiStore` already treats each provider as a store with one interface. A **live store** that calls a
  provider API at query time and emits the same lines plugs in without changing `analysis.py`.
- **Pooled** for trends, anomalies, month end and cross-cloud showback; **live** for today's spend, resource-level
  drill-down and recommendations. `data_status` shows each provider's coverage so the agent knows when to go live.
- **Skills** carry provider knowledge that needs judgement and changes with context. They decide *what to ask and how
  to read it*; code decides *how numbers are computed and merged*.

## 5. Next steps

In priority order (details and sources: the multi-cloud design write-up for this project):

1. Recommendation specialists behind AgentCore Gateway (read-only MCP: AWS Billing and Cost Management MCP server,
   Azure Advisor tools, Google Recommender), plus per-provider skills; per-cloud sub-agents only for commitment logic.
2. A live store prototype (Azure Cost Management Query API) to compare freshness and numbers with the FOCUS export.
3. AWS on Data Exports FOCUS 1.2 where the payer account allows (amortized cost, no paid Cost Explorer calls); either
   the collector or `FocusIngest` writes `finops/v1/`, never both.
4. Parquet + Athena / DuckDB and a guarded `sql_query` tool when data grows.
5. FOCUS 1.3/1.4 columns (allocation, data recency and completeness) and 1.5 AI token columns.
6. Golden-question evaluation (AgentCore Evaluations) and an action layer (Jira / Slack with human approval).

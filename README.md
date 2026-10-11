# AWS FinOps agent (AgentCore)

A FinOps analyst hosted on Amazon Bedrock AgentCore Runtime. A daily collector stores Cost Explorer data in S3;
the agent answers questions about it - LLM vs infrastructure vs networking cost, per model and token type,
per project, trends, anomalies and month-end projections. Every number comes from deterministic code
(`app/finops_agent/finops/analysis.py`); the model only chooses tools and writes the answer.

```
EventBridge (daily 01:00 UTC = 09:00 Asia/Taipei)
  -> Lambda "Collector" (finops/lambda_handler.py)
       Cost Explorer  GetCostAndUsage DAILY x SERVICE x USAGE_TYPE   (rolling 7 days, revised days replaced)
                      GetTags -> GetCostAndUsage by project tag/cost category (only when one is active)
       CloudWatch     AWS/Bedrock-AgentCore CPUUsed-vCPUHours / MemoryUsed-GBHours per runtime
       Tagging API    runtime -> project
  -> S3  finops/v1/status.json, finops/v1/months/YYYY-MM.json
           ^ read-only
AgentCore Runtime "finops_agent" (Strands + Claude Sonnet 5.5 on Bedrock, 10 tools)
```

Optional, multi-cloud: Azure / Google Cloud / OCI **FOCUS** exports land in `landing/<provider>/`, a second Lambda
aggregates them into `finops/v1/<provider>/` in the same format, and every tool gets a `provider` dimension and filter
(see [Multi-cloud](#multi-cloud-focus)).

Design notes - how the agent is controlled (prompt, tools, deterministic code, guardrails), what FOCUS is, and why
cross-cloud numbers are pooled rather than merged by skills: [docs/design.md](docs/design.md).

## Cost taxonomy

Applied when data is read (`finops/classify.py`), so a rule change re-classifies all history.

| Group | Categories | What lands there |
| --- | --- | --- |
| `llm` | llm | Bedrock model inference (Marketplace "Amazon Bedrock Edition" models and first-party models); split by model and token type: input / output / cache_read / cache_write / provisioned, and global vs regional |
| `infrastructure` | ai_agent_platform, compute, storage, database | AgentCore (runtime, memory, gateway, tools), KB/guardrails; EC2, Lambda, ECS/EKS, SageMaker; S3, EBS, EFS, ECR; RDS, DynamoDB, OpenSearch... |
| `networking` | networking | Data transfer, NAT, public IPv4, VPC endpoints, load balancers, Route 53, CloudFront, API Gateway - **from any service** (e.g. AgentCore `DataTransfer-*` is networking, not AgentCore) |
| `platform_services` | observability, security_and_management, analytics, app_integration | CloudWatch/X-Ray/CloudTrail; KMS, Secrets Manager, Cost Explorer API, Config...; Athena/Glue; SQS/SNS/Step Functions/Amplify |
| `support_and_tax` | support, tax | Support plan and tax: monthly charges on the 1st, kept out of anomaly detection and treated as fixed in projections |
| `other` | other | Anything not matched - check `cost_summary(group="other", group_by="service")` and add a rule |

## Project grouping

The collector picks the best source available, per day:

1. **Cost Category** (`COST_CATEGORY`, defined in the payer account) - recommended long-term: rules can combine tags,
   accounts and services, and give untagged spend a home ("Shared").
2. **Cost allocation tag** - the first of `PROJECT_TAG_KEYS` (`project,Project,agentcore:project-name,app`) that is
   *active* in Cost Explorer.
3. **Usage allocation** (fallback, works today): AgentCore Runtime vCPU/memory cost is split per runtime by its
   measured CloudWatch vCPU-hours / GB-hours; a runtime maps to a project by its tag (or the `<Project>_<agent>` name
   the AgentCore CLI gives it). The rest is reported as `(unallocated)`, with `allocationCoverage`.

**The development account is a linked member account** of an Organization (Innovation Sandbox). Cost allocation tags
can only be activated in the payer account (`ListCostAllocationTags` -> AccessDenied, `GetTags` -> empty), so
the agent currently runs in mode 3. To get real per-project numbers:

- Ask the payer admin to activate `project` and `agentcore:project-name` as cost allocation tags (Billing ->
  Cost allocation tags). Activation is not retroactive; the collector switches to tag mode by itself on the next run.
- Tag every resource with `project=<name>` (AgentCore CLI already adds `agentcore:project-name` to its stacks).
- **LLM cost per project:** Bedrock bills per model, not per caller. Give each project its own *application inference
  profile* (tagged `project=<name>`) and call that profile ARN as `MODEL_ID`; once the tag is active, LLM cost splits
  by project in Cost Explorer. Until then LLM cost stays in `(unallocated)`.

## Using it

```bash
export PATH=$HOME/.npm-global/bin/bin:$PATH
agentcore invoke "What did we spend on LLMs vs infrastructure vs networking in the last 30 days?"
agentcore invoke '{"action": "report", "days": 30}'      # deterministic JSON, no model call
agentcore invoke '{"prompt": "LLM cost last week?", "events": true}'   # also streams tool calls + JSON results
python3 scripts/invoke.py "Which model costs most and how good is our prompt-cache usage?"
```

## Web UI (`web/`)

A React dashboard hosted on Amplify, in the same stack (`agentcore/cdk/lib/finops-ui.ts`):

```
browser -- identity pool guest identity (classic flow) -> temporary credentials, allowed only
           bedrock-agentcore:InvokeAgentRuntime on this runtime   (guestAccess: false -> Cognito user-pool sign-in)
        -- InvokeAgentRuntime (AgentCore allows CORS)   {"action": "report"}            -> dashboard, no model call
                                                         {"prompt": ..., "events": true} -> streamed answer + tool results
```

The dashboard (period presets, daily cost by group, category, LLM model x token type, top services, anomalies,
projects, month-end projection) is drawn from the deterministic report. In the "Ask" panel the agent streams its answer
and, with `"events": true`, every tool call and its JSON result, which the UI charts directly - so charts show numbers
from `finops/analysis.py`, not from the model's prose. "Ask why" on an anomaly sends a follow-up question.

```bash
agentcore deploy -y                    # creates Cognito, the identity pool and the Amplify app (once)
scripts/deploy-ui.sh                   # build web/ with the stack outputs and publish it (manual Amplify deployment)
```

**Guest access is on by default** (`guestAccess` in `finops-ui.ts`): anyone with the URL can read the cost data and
ask questions (model tokens). Password-protect the branch with Amplify access control, or set `guestAccess: false`,
`agentcore deploy` and `scripts/deploy-ui.sh` to require sign-in (users created with `admin-create-user`).

Local development: copy `web/.env.example` to `web/.env.local` with the stack outputs, then `cd web && npm run dev`.

Tools: `data_status`, `cost_summary`, `daily_trend`, `plot_cost` (any chart the user asks for: x = date / month /
any dimension, split by a series dimension; returns a chart spec the UI renders), `llm_costs`, `project_costs`,
`compare_periods`, `find_anomalies`, `month_end_projection` (optionally AWS's forecast - one paid CE request, max 3 per
session), `project_resources` (AWS only).

### Tool-result contract with the UI

With `"events": true` every `tool_result` event has `status` `success` or `error`, and `result` is always a JSON
object; a failed call (bad arguments rejected by Strands, an unknown argument, an exception) is `{"error": "..."}`,
never free text. Arguments a tool does not declare are rejected (`StrictToolArgs` hook) instead of being silently
dropped. In the browser each tool result, dashboard card and answer has its own error boundary and every result is
shape-checked before it is charted; anything unexpected is shown as a table / JSON instead of breaking the page.

> Before this, one failed tool call (e.g. the model passing `top_n: "all"` while plotting) reached the UI as
> `{"text": "Error: ..."}`, the chart component read `result.period.start`, and React unmounted the whole app: blank
> page. `cost_summary(group_by="date,…")` also ranked (date, value) pairs, so a 30-day chart lost days and series.

Backfill or re-collect (the daily rule only re-reads the last 7 days):

```bash
FN=$(aws cloudformation describe-stacks --stack-name AgentCore-FinopsAgent-default \
      --query "Stacks[0].Outputs[?contains(OutputKey,'CollectorFunctionName')].OutputValue" --output text)
aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out \
    --payload '{"backfillDays": 365}' /dev/stdout
```

## Multi-cloud (FOCUS)

All three clouds export billing data in FOCUS (AWS Data Exports FOCUS 1.2, Azure Cost Management "FOCUS" dataset,
Google Cloud Billing FOCUS export to BigQuery). The agent keeps one store per provider and reads them as one dataset:

```
Azure Cost Management export (FOCUS, CSV/Parquet) -> Blob ---\
Google Cloud FOCUS export (BigQuery) -> EXPORT DATA -> GCS ----+-- AWS DataSync --> s3://<bucket>/landing/<provider>/<run>/
OCI FOCUS reports bucket --------------------------------------/                          |  manifest.json / _SUCCESS
                                                                                           v
                                     Lambda FocusIngest (finops/focus_handler.py) -> finops/v1/<provider>/months/*.json
AWS keeps the Cost Explorer collector (finops/v1/)                                         |
                                     agent: MultiStore(aws, azure, gcp) -> same tools, + provider dimension / filter
```

1. Deploy with the providers you have: `FINOPS_FOCUS_PROVIDERS=azure,gcp agentcore deploy -y`
   (optional: `FINOPS_FOCUS_COST_METRIC=EffectiveCost` for amortized commitments; `FINOPS_REPORTING_CURRENCY=USD`
   and `FINOPS_FX_RATES='{"TWD":0.031}'` when providers bill in different currencies - otherwise mixed currencies
   are refused rather than summed).
2. **Azure:** Cost Management -> Exports -> dataset *Cost and usage details (FOCUS)*, daily month-to-date, to a
   storage account (file partitioning on: the run writes `manifest.json`).
3. **Google Cloud:** enable the FOCUS export to BigQuery, then a scheduled query
   writing each run to a **new directory** (e.g. `gs://<bucket>/focus/<run date>/part-*.csv`):
   `EXPORT DATA OPTIONS(uri=..., format='CSV', header=true) AS SELECT BillingPeriodStart, BillingPeriodEnd,
   ChargePeriodStart, BilledCost, EffectiveCost, BillingCurrency, ServiceName, ServiceCategory, ServiceSubcategory,
   SkuMeter, RegionId, SubAccountId, ChargeCategory, ConsumedQuantity, ConsumedUnit, TO_JSON_STRING(Tags) AS Tags
   FROM <focus table> WHERE BillingPeriodStart >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 1 MONTH))`
   (current and previous billing period, so late revisions of last month are picked up too).
4. Copy each run to `landing/<provider>/<run>/` (AWS DataSync reads Azure Blob and Cloud Storage). Ingestion starts
   on the run's `manifest.json` / `_SUCCESS`, or invoke `FocusIngest` with
   `{"provider": "gcp", "uri": "s3://<bucket>/landing/gcp/<run>/"}` - e.g. from an EventBridge rule on the DataSync
   task execution's SUCCESS. Only the files directly in the run directory are read - for Azure exactly the files
   its manifest lists, and the invocation fails (and is retried) until all of them have arrived.
   `FocusIngest` only writes the configured providers' stores (`finops/v1/<provider>/`), never the AWS one.

Days of the run's billing period(s) replace the stored days; corrections to an earlier period are booked on the
first day of the period that invoices them (`chargeCategory` "... (out of period)"), so re-ingesting a run is
idempotent. A provider whose data ends more than 3 days before the newest one is reported as stale (`data_status`,
dashboard banner) instead of pulling everyone's default period back.

FOCUS lines are classified by `classify.classify_focus` into the same taxonomy (Azure OpenAI / Foundry and Vertex AI
token meters -> `llm` with model and token type; FOCUS `ServiceCategory` for the rest; egress/NAT/LB meters ->
`networking`). Projects come from FOCUS `Tags` (`PROJECT_TAG_KEYS`), so cross-cloud showback works by tag.
The default period ends at the **earliest** provider's last day, so cross-cloud totals never mix a complete day of
one cloud with a missing day of another; `data_status` shows each provider's coverage.

## Cost of running it

- Cost Explorer API: USD 0.01 per request; 2-3 per daily run (about USD 1/month), 0-3 per conversation only when
  AWS's forecast is asked for. Agent questions read S3, not Cost Explorer.
- Lambda / S3 / EventBridge: well inside free tier at this volume.
- Agent: AgentCore Runtime consumption + Sonnet 5.5 tokens per question (typically a few cents).

## Local development (no deployment)

Run the agent on your machine against a synthetic dataset and point the web UI at it - the same payloads and event
stream as the deployed runtime. Only the chat needs AWS credentials (Bedrock `InvokeModel` for `MODEL_ID`); the
dashboard (`action: report`) makes no model call.

```bash
python3 scripts/seed_local_data.py .local-data --multicloud      # AWS + Azure + GCP sample data (gitignored)

cd app/finops_agent                                              # terminal 1: the agent on :8080
DATA_URI=$PWD/../../.local-data/ DATA_PROVIDERS=aws,azure,gcp AWS_REGION=us-east-1 \
  uv run python main.py

cd web && cp .env.local-agent.example .env.local && npm run dev  # terminal 2: UI on :5173, proxies /invocations
```

Without the UI: `curl -N localhost:8080/invocations -H 'Content-Type: application/json'
-H 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-session-000000000000000000001'
-d '{"prompt": "Plot daily LLM cost by model and provider", "events": true}'`.
Remove `.env.local` (or set `VITE_LOCAL_AGENT=false`) to go back to the deployed runtime; local-agent mode only
exists in `npm run dev`, never in a build.

## Development

```bash
cd app/finops_agent
UV_PROJECT_ENVIRONMENT=/tmp/finops-venv uv run --extra dev python -m pytest tests     # offline, fakes for AWS
cp agentcore/aws-targets.example.json agentcore/aws-targets.json   # once: set your account id
agentcore validate && agentcore deploy -y          # from the project root
```

This folder is on gcsfuse, which cannot do the renames uv/npm need and is slow with many small files. Keep build state
on local disk via symlinks (recreate after a new session; `/tmp` is not persistent):

```bash
mkdir -p /tmp/finops-cdk /tmp/finops-agentcore-cache
(cd agentcore/cdk && cp package*.json /tmp/finops-cdk/ && (cd /tmp/finops-cdk && npm ci) && ln -sfn /tmp/finops-cdk/node_modules node_modules)
ln -sfn /tmp/finops-agentcore-cache agentcore/.cache
```

`agentcore/cdk/tsconfig.json` sets `preserveSymlinks` for this reason.

| Path | What |
| --- | --- |
| `app/finops_agent/main.py` | AgentCore entrypoint (`prompt` -> agent stream, `action: report` -> JSON) |
| `app/finops_agent/finops/` | `classify` (taxonomy), `collect` (collector), `focus` + `focus_handler` (FOCUS ingestion), `store` (S3/local, `MultiStore`), `analysis` (numbers), `tools` (Strands tools), `lambda_handler` |
| `agentcore/cdk/lib/finops-data.ts` | Bucket, collector Lambda, daily rule, runtime permissions (wired in `cdk-stack.ts`) |
| `agentcore/agentcore.json` | Runtime definition and env vars (`MODEL_ID` etc.) |

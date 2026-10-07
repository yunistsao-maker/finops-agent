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
AgentCore Runtime "finops_agent" (Strands + Claude Sonnet 5.5 on Bedrock, 9 tools)
```

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
python3 scripts/invoke.py "Which model costs most and how good is our prompt-cache usage?"
```

Tools: `data_status`, `cost_summary`, `daily_trend`, `llm_costs`, `project_costs`, `compare_periods`,
`find_anomalies`, `month_end_projection` (optionally AWS's forecast - one paid CE request, max 3 per session),
`project_resources`.

Backfill or re-collect (the daily rule only re-reads the last 7 days):

```bash
FN=$(aws cloudformation describe-stacks --stack-name AgentCore-FinopsAgent-default \
      --query "Stacks[0].Outputs[?contains(OutputKey,'CollectorFunctionName')].OutputValue" --output text)
aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out \
    --payload '{"backfillDays": 365}' /dev/stdout
```

## Cost of running it

- Cost Explorer API: USD 0.01 per request; 2-3 per daily run (about USD 1/month), 0-3 per conversation only when
  AWS's forecast is asked for. Agent questions read S3, not Cost Explorer.
- Lambda / S3 / EventBridge: well inside free tier at this volume.
- Agent: AgentCore Runtime consumption + Sonnet 5.5 tokens per question (typically a few cents).

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
| `app/finops_agent/finops/` | `classify` (taxonomy), `collect` (collector), `store` (S3/local), `analysis` (numbers), `tools` (Strands tools), `lambda_handler` |
| `agentcore/cdk/lib/finops-data.ts` | Bucket, collector Lambda, daily rule, runtime permissions (wired in `cdk-stack.ts`) |
| `agentcore/agentcore.json` | Runtime definition and env vars (`MODEL_ID` etc.) |

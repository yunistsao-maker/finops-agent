"""Map one Cost Explorer line (service + usage type) to a FinOps category.

Pure Python, no AWS calls: shared by the collector Lambda and the agent, and applied when data is READ,
so changing a rule re-classifies all stored history without re-collecting it.

Two levels:
    group     llm | infrastructure | networking | platform_services | support_and_tax | other
    category  the finer bucket inside a group (see CATEGORIES)

Networking is cross-cutting: data transfer, NAT, public IPv4, endpoints and load balancers are networking
whichever service bills them (EC2, S3, AgentCore, ...).  Everything else is decided by the service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# category -> group
CATEGORIES: dict[str, str] = {
    "llm": "llm",
    "ai_agent_platform": "infrastructure",
    "compute": "infrastructure",
    "storage": "infrastructure",
    "database": "infrastructure",
    "networking": "networking",
    "observability": "platform_services",
    "security_and_management": "platform_services",
    "analytics": "platform_services",
    "app_integration": "platform_services",
    "support": "support_and_tax",
    "tax": "support_and_tax",
    "other": "other",
}

CATEGORY_LABELS: dict[str, str] = {
    "llm": "LLM (model inference)",
    "ai_agent_platform": "AI agent platform (AgentCore, KB, guardrails)",
    "compute": "Compute",
    "storage": "Storage",
    "database": "Database",
    "networking": "Networking & data transfer",
    "observability": "Observability",
    "security_and_management": "Security & management",
    "analytics": "Analytics",
    "app_integration": "Application integration & hosting",
    "support": "AWS Support",
    "tax": "Tax",
    "other": "Other",
}

GROUPS = ("llm", "infrastructure", "networking", "platform_services", "support_and_tax", "other")

# usage-type prefix -> region.  A usage type without a known prefix is billed in us-east-1 (AWS convention),
# except lines that have no usage type at all (support, tax): those are "global".
REGION_PREFIXES: dict[str, str] = {
    "USE1": "us-east-1", "USE2": "us-east-2", "USW1": "us-west-1", "USW2": "us-west-2",
    "CAN1": "ca-central-1", "CAW1": "ca-west-1", "SAE1": "sa-east-1", "MXC1": "mx-central-1",
    "EU": "eu-west-1", "EUW1": "eu-west-1", "EUW2": "eu-west-2", "EUW3": "eu-west-3", "EUC1": "eu-central-1",
    "EUC2": "eu-central-2", "EUN1": "eu-north-1", "EUS1": "eu-south-1", "EUS2": "eu-south-2",
    "APN1": "ap-northeast-1", "APN2": "ap-northeast-2", "APN3": "ap-northeast-3", "APS1": "ap-southeast-1",
    "APS2": "ap-southeast-2", "APS3": "ap-south-1", "APS4": "ap-southeast-3", "APS5": "ap-south-2",
    "APS6": "ap-southeast-4", "APS7": "ap-southeast-5", "APS8": "ap-southeast-7", "APE1": "ap-east-1",
    "APE2": "ap-east-2", "TWN1": "ap-east-2",
    "MES1": "me-south-1", "MEC1": "me-central-1", "ILC1": "il-central-1", "AFS1": "af-south-1",
    "UGW1": "us-gov-west-1", "UGE1": "us-gov-east-1",
}

_NETWORK_USAGE = re.compile(
    r"DataTransfer|DataXfer|-Out-Bytes|-In-Bytes|NatGateway|PublicIPv4|VpcEndpoint|VPCE|TransitGateway"
    r"|LoadBalancerUsage|LCUUsage|LoadBalancer|ClientVPN|VPN-Usage|IdleAddress|ElasticIP|CloudFront",
    re.IGNORECASE,
)
_STORAGE_USAGE = re.compile(r"EBS:|EBSOptimized|SnapshotUsage|TimedStorage|VolumeUsage", re.IGNORECASE)
_TOKEN_USAGE = re.compile(r"token", re.IGNORECASE)

_SERVICE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("ai_agent_platform", ("Bedrock AgentCore", "Amazon Kendra", "Amazon Q Business", "Amazon Q Developer", "Amazon Lex", "Amazon Comprehend",
                           "Amazon Textract", "Amazon Transcribe", "Amazon Polly", "Amazon Translate", "Amazon Rekognition")),
    ("networking", ("Virtual Private Cloud", "Elastic Load Balancing", "CloudFront", "Route 53", "Direct Connect",
                    "Global Accelerator", "AWS Data Transfer", "Transit Gateway", "Network Firewall", "VPC Lattice",
                    "API Gateway")),
    ("storage", ("Simple Storage Service", "Elastic File System", "FSx", "AWS Backup", "Glacier", "Storage Gateway", "S3 Glacier",
                 "Elastic Container Registry", "EC2 Container Registry")),
    ("compute", ("Elastic Compute Cloud", "EC2", "AWS Lambda", "Elastic Container Service", "Elastic Kubernetes",
                 "Fargate", "AWS Batch", "Lightsail", "App Runner", "Elastic Beanstalk", "SageMaker")),
    ("database", ("Relational Database", "RDS", "DynamoDB", "ElastiCache", "Redshift", "DocumentDB", "Neptune",
                  "OpenSearch", "MemoryDB", "Keyspaces", "Timestream", "Aurora", "Database Migration")),
    ("observability", ("CloudWatch", "X-Ray", "CloudTrail", "Managed Grafana", "Managed Service for Prometheus")),
    ("security_and_management", ("Key Management", "Secrets Manager", "WAF", "Shield", "GuardDuty", "Security Hub",
                                 "Inspector", "Macie", "AWS Config", "Cognito", "Certificate Manager", "Systems Manager",
                                 "Cost Explorer", "AWS Budgets", "CloudFormation", "IAM", "Detective", "Service Catalog",
                                 "Control Tower", "Organizations", "Resource Groups")),
    ("analytics", ("Athena", "AWS Glue", "Kinesis", "EMR", "QuickSight", "Data Firehose", "Managed Streaming for Apache Kafka",
                   "Lake Formation", "Data Exports", "Cost and Usage Report")),
    ("app_integration", ("Simple Queue Service", "Simple Notification Service", "EventBridge", "Step Functions",
                         "AWS Amplify", "Simple Email Service", "AppSync", "Amazon MQ", "AWS AppConfig")),
]


@dataclass(frozen=True)
class Classification:
    group: str
    category: str
    subcategory: str  # model name for llm, product area for AgentCore, the service otherwise
    region: str
    model: str | None = None
    token_type: str | None = None  # input | output | cache_read | cache_write | provisioned | other (llm only)


def region_of(usage_type: str) -> str:
    if not usage_type or usage_type == "NoUsageType":
        return "global"
    head = usage_type.split("-", 1)[0].split(":", 1)[0]
    if head in REGION_PREFIXES:
        return REGION_PREFIXES[head]
    return "us-east-1"


def _strip_region(usage_type: str) -> str:
    head, sep, rest = usage_type.partition("-")
    return rest if sep and head in REGION_PREFIXES else usage_type


def token_type_of(usage_type: str) -> str:
    u = usage_type.lower().replace("-", "_")
    if "cacheread" in u or "cache_read" in u:
        return "cache_read"
    if "cachewrite" in u or "cache_write" in u:
        return "cache_write"
    if "provisioned" in u or "modelunit" in u:
        return "provisioned"
    if "input" in u:
        return "input"
    if "output" in u:
        return "output"
    return "other"


def is_llm(service: str, usage_type: str) -> bool:
    if "(Amazon Bedrock Edition)" in service:  # Marketplace-billed models (Anthropic, ...)
        return True
    if service in ("Amazon Bedrock", "Bedrock"):
        u = usage_type.lower()
        return bool(_TOKEN_USAGE.search(usage_type)) or "provisioned" in u or "modelunit" in u or "image" in u
    return False


def model_of(service: str, usage_type: str) -> str:
    if "(Amazon Bedrock Edition)" in service:
        return service.replace("(Amazon Bedrock Edition)", "").strip()
    # Amazon Bedrock first-party usage types look like "USE1-NovaPro-input-tokens" or "USE1-TitanEmbedV2-Text-input-tokens"
    rest = _strip_region(usage_type)
    m = re.split(r"[-_](?:input|output|cache|batch|provisioned|image|tokens)", rest, maxsplit=1, flags=re.IGNORECASE)
    return (m[0] or rest or "unknown").strip("-_") or "unknown"


def agentcore_area(usage_type: str) -> str:
    rest = _strip_region(usage_type)
    area = rest.split(":", 1)[0]
    return {"WebSearchTool": "Tools", "Browser": "Tools", "CodeInterpreter": "Tools"}.get(area, area or "AgentCore")


def classify(service: str, usage_type: str) -> Classification:
    service = service or ""
    usage_type = usage_type or ""
    region = region_of(usage_type)

    if service == "Tax":
        return Classification("support_and_tax", "tax", "Tax", "global")
    if "Support" in service and service.startswith("AWS"):
        return Classification("support_and_tax", "support", service, "global")

    if is_llm(service, usage_type):
        model = model_of(service, usage_type)
        return Classification("llm", "llm", model, region, model=model, token_type=token_type_of(usage_type))

    if _NETWORK_USAGE.search(usage_type):
        return Classification("networking", "networking", service, region)

    if service in ("Amazon Bedrock", "Bedrock"):  # knowledge bases, guardrails, flows, evaluation
        return Classification("infrastructure", "ai_agent_platform", "Amazon Bedrock", region)

    if ("Elastic Compute Cloud" in service or service == "EC2 - Other") and _STORAGE_USAGE.search(usage_type):
        return Classification("infrastructure", "storage", "Amazon EBS", region)

    for category, needles in _SERVICE_RULES:
        if any(n in service for n in needles):
            sub = agentcore_area(usage_type) if "AgentCore" in service else service
            return Classification(CATEGORIES[category], category, sub, region)

    return Classification("other", "other", service or "unknown", region)

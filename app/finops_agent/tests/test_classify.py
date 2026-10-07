import pytest

from finops.classify import classify

# (service, usage type) pairs as Cost Explorer reports them in the development account
CASES = [
    ("Claude Opus 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_cache_read_tokens_standard-Units", "llm", "llm", "cache_read"),
    ("Claude Opus 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_cache_write_tokens_standard-Units", "llm", "llm", "cache_write"),
    ("Claude Sonnet 4.6 (Amazon Bedrock Edition)", "USE1-MP:USE1_InputTokenCount_Global-Units", "llm", "llm", "input"),
    ("Claude Sonnet 4.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_CacheReadInputTokenCount-Units", "llm", "llm", "cache_read"),
    ("Claude Haiku 4.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_OutputTokenCount-Units", "llm", "llm", "output"),
    ("Amazon Bedrock", "USE1-NovaPro-input-tokens", "llm", "llm", "input"),
    ("Amazon Bedrock", "USE1-Guardrails-ContentPolicyUnits", "infrastructure", "ai_agent_platform", None),
    ("Amazon Bedrock AgentCore", "USE1-Runtime:Consumption-based:vCPU", "infrastructure", "ai_agent_platform", None),
    ("Amazon Bedrock AgentCore", "USE1-Memory:Consumption-based:Short-Term-Memory", "infrastructure", "ai_agent_platform", None),
    ("Amazon Bedrock AgentCore", "DataTransfer-Regional-Bytes", "networking", "networking", None),
    ("AWS Amplify", "APN1-DataTransferOut", "networking", "networking", None),
    ("AWS Amplify", "APN1-DataStorage", "platform_services", "app_integration", None),
    ("Amazon Simple Storage Service", "Requests-Tier1", "infrastructure", "storage", None),
    ("AmazonCloudWatch", "USE1-Application-Signals-Bytes", "platform_services", "observability", None),
    ("AWS Key Management Service", "us-east-1-KMS-Keys", "platform_services", "security_and_management", None),
    ("AWS Cost Explorer", "USE1-APIRequest", "platform_services", "security_and_management", None),
    ("AWS Business Support+", "NoUsageType", "support_and_tax", "support", None),
    ("Tax", "NoUsageType", "support_and_tax", "tax", None),
    ("Amazon Elastic Compute Cloud - Compute", "USE1-BoxUsage:t3.medium", "infrastructure", "compute", None),
    ("EC2 - Other", "USE1-EBS:VolumeUsage.gp3", "infrastructure", "storage", None),
    ("EC2 - Other", "USE1-NatGateway-Hours", "networking", "networking", None),
    ("Amazon Virtual Private Cloud", "USE1-PublicIPv4:InUseAddress", "networking", "networking", None),
    ("Amazon Elastic Compute Cloud - Compute", "USE1-USW2-AWS-Out-Bytes", "networking", "networking", None),
    ("Amazon EC2 Container Registry (ECR)", "USE1-TimedStorage-ByteHrs", "infrastructure", "storage", None),
    ("Amazon QuickSight", "USE1-QS-User-Enterprise-Month", "platform_services", "analytics", None),
    ("Amazon Relational Database Service", "USE1-InstanceUsage:db.t4g.micro", "infrastructure", "database", None),
    ("AWS Lambda", "USE1-Lambda-GB-Second", "infrastructure", "compute", None),
    ("Some New Service", "USE1-Thing", "other", "other", None),
]


@pytest.mark.parametrize("service,usage_type,group,category,token_type", CASES)
def test_classify(service, usage_type, group, category, token_type):
    c = classify(service, usage_type)
    assert (c.group, c.category, c.token_type) == (group, category, token_type)


def test_llm_model_names():
    assert classify("Claude Sonnet 5.5 (Amazon Bedrock Edition)", "USE1-MP:USE1_input_tokens_standard-Units").model == "Claude Sonnet 5.5"
    assert classify("Amazon Bedrock", "USW2-NovaLite-output-tokens").model == "NovaLite"


def test_regions():
    assert classify("AWS Amplify", "APN1-DataStorage").region == "ap-northeast-1"
    assert classify("Amazon Simple Storage Service", "Requests-Tier1").region == "us-east-1"
    assert classify("Tax", "NoUsageType").region == "global"
    assert classify("Amazon Bedrock AgentCore", "USE1-Runtime:Consumption-based:vCPU").subcategory == "Runtime"
    assert classify("Amazon Bedrock AgentCore", "USE1-WebSearchTool:Consumption-based:Queries").subcategory == "Tools"

import os

from botocore.config import Config
from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Bedrock model via the runtime's IAM role.  Claude 5.5: use an inference-profile id and do not set
    temperature/top_p (non-default values are rejected)."""
    return BedrockModel(
        model_id=os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5-5"),
        max_tokens=int(os.environ.get("MAX_TOKENS", "8000")),
        boto_client_config=Config(read_timeout=300, connect_timeout=15, retries={"max_attempts": 4, "mode": "adaptive"},
                                  user_agent_extra="aws-finops-agent/0.1"),
    )

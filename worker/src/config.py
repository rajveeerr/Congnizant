from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    dynamodb_endpoint: str = "http://localhost:8001"
    aws_region: str = "us-east-1"

    # Bedrock — flip to "real" once AWS creds are available
    bedrock_mode: str = "mock"
    bedrock_region: str = "us-east-1"
    bedrock_text_model: str = "anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_embed_model: str = "amazon.titan-embed-text-v2:0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

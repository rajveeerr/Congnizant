from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    dynamodb_endpoint: str = "http://localhost:8001"
    aws_region: str = "us-east-1"

    api_key: str = "test-key"
    # Directory containing per-worker SQLite trace files. Server globs
    # everything in this dir matching agent_traces_*.db.
    traces_db_dir: str = "/app/traces"
    opensearch_host: str = "opensearch"
    opensearch_port: int = 9200

    # Backpressure / rate limits (Step 4)
    # Reject new events when the worker queue is this deep — protects Redis
    # and prevents the system from accepting work it can't drain.
    max_queue_depth: int = 10000
    # Per-customer fixed-window limit, applied inside the events route.
    # Stops one customer from monopolising the worker pool.
    max_events_per_customer_per_min: int = 100
    # Per-API-key fixed-window limit, applied as middleware on every request.
    max_requests_per_key_per_min: int = 1000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

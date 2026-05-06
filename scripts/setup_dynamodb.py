"""Create DynamoDB tables in DynamoDB Local.

Idempotent — safe to re-run. Run inside the server container so the same
DYNAMODB_ENDPOINT env var is used:

  make setup-db
  # or
  docker compose exec server python /app/scripts/setup_dynamodb.py
"""

import os

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8001")
REGION = os.getenv("AWS_REGION", "us-east-1")

dynamodb = boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name=REGION)

PK_SK = [
    {"AttributeName": "PK", "KeyType": "HASH"},
    {"AttributeName": "SK", "KeyType": "RANGE"},
]
PK_SK_ATTRS = [
    {"AttributeName": "PK", "AttributeType": "S"},
    {"AttributeName": "SK", "AttributeType": "S"},
]
STATUS_INDEX_KEYS = [
    {"AttributeName": "status", "KeyType": "HASH"},
    {"AttributeName": "created_at", "KeyType": "RANGE"},
]
STATUS_INDEX_ATTRS = [
    {"AttributeName": "status", "AttributeType": "S"},
    {"AttributeName": "created_at", "AttributeType": "S"},
]


TABLES = [
    {
        "TableName": "customer_events",
        "KeySchema": PK_SK,
        "AttributeDefinitions": PK_SK_ATTRS + STATUS_INDEX_ATTRS,
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "status-index",
                "KeySchema": STATUS_INDEX_KEYS,
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "customer_consent",
        "KeySchema": PK_SK,
        "AttributeDefinitions": PK_SK_ATTRS,
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "customer_auth",
        "KeySchema": PK_SK,
        "AttributeDefinitions": PK_SK_ATTRS,
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "jobs",
        "KeySchema": PK_SK,
        "AttributeDefinitions": PK_SK_ATTRS + STATUS_INDEX_ATTRS,
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "status-index",
                "KeySchema": STATUS_INDEX_KEYS,
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "product_catalog",
        "KeySchema": PK_SK,
        "AttributeDefinitions": PK_SK_ATTRS,
        "BillingMode": "PAY_PER_REQUEST",
    },
]


def create_or_skip(spec: dict) -> str:
    name = spec["TableName"]
    try:
        dynamodb.create_table(**spec)
        return f"created  {name}"
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            return f"exists   {name}"
        raise


def enable_ttl(table_name: str, attribute: str = "expires_at") -> str:
    """Enable DynamoDB TTL on a table. Idempotent."""
    try:
        dynamodb.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={
                "Enabled": True,
                "AttributeName": attribute,
            },
        )
        return f"ttl on   {table_name}.{attribute}"
    except ClientError as e:
        # Already enabled with the same attribute → harmless
        msg = str(e)
        if "TimeToLive is already enabled" in msg or "already an active" in msg:
            return f"ttl skip {table_name} (already enabled)"
        raise


def main() -> None:
    print(f"endpoint: {ENDPOINT}")
    for spec in TABLES:
        print(create_or_skip(spec))
    # TTL on events so old data auto-expires per the customer's retention setting
    print(enable_ttl("customer_events", "expires_at"))


if __name__ == "__main__":
    main()

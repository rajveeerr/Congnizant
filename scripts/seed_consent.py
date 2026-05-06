"""Seed consent records for testing.

Phase 8 adds the real POST /consent endpoint; until then this script
inserts records directly so privacy_tool has something to check.

Usage: make seed-consent
"""

import os

from shared.dynamo import DynamoClient
from shared.schemas import ConsentRecord

ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:8001")
REGION = os.getenv("AWS_REGION", "us-east-1")


def main() -> None:
    dynamo = DynamoClient(endpoint=ENDPOINT, region=REGION)

    customers = [
        ("cust_1", {"personalization", "analytics"}),
        ("cust_2", {"personalization"}),
        ("cust_no_consent", set()),  # row exists but no scopes granted
    ]

    for customer_id, scopes in customers:
        consent = ConsentRecord(customer_id=customer_id, scopes=scopes)
        dynamo.put_consent(consent.model_dump())
        scope_label = sorted(scopes) if scopes else "<none>"
        print(f"seeded consent  cust={customer_id:18}  scopes={scope_label}")


if __name__ == "__main__":
    main()

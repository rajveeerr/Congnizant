"""End-to-end privacy verification.

  1. Create consent for a fresh test customer.
  2. Ingest a few events (server should accept).
  3. Wait briefly for the worker to process them.
  4. Verify state in DynamoDB + OpenSearch + Redis.
  5. DELETE /customer/{id}.
  6. Verify everything is gone.

  Also verifies:
  - Ungated customer (no consent) gets 403 from /events
  - Consent with no personalization scope gets 403

Run inside the server container so it can reach all backends:
  make test-privacy
"""

import json
import os
import time
import urllib.request
import urllib.error
import uuid
from urllib.parse import urlencode

import boto3
from opensearchpy import OpenSearch

BASE_URL = os.getenv("HYPERPERSONA_BASE_URL", "http://server:8000")
API_KEY = os.getenv("API_KEY", "test-key")
DDB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://dynamodb-local:8000")
REGION = os.getenv("AWS_REGION", "us-east-1")
OS_HOST = os.getenv("OPENSEARCH_HOST", "opensearch")
OS_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

CUSTOMER = "cust_privacy_test"
NO_CONSENT = "cust_no_consent_test"
NO_SCOPE = "cust_no_scope_test"


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("X-API-Key", API_KEY)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode() or "null"
            return resp.status, json.loads(text)
    except urllib.error.HTTPError as e:
        text = e.read().decode() or "null"
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {"raw": text}


def _section(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> None:
    import redis as redis_module

    dynamodb = boto3.resource("dynamodb", endpoint_url=DDB_ENDPOINT, region_name=REGION)
    os_client = OpenSearch(
        hosts=[{"host": OS_HOST, "port": OS_PORT}],
        use_ssl=False,
        verify_certs=False,
    )
    redis_client = redis_module.from_url(REDIS_URL, decode_responses=True)

    # Clean up any leftover state from a previous failed run.
    _request("DELETE", f"/customer/{CUSTOMER}")
    _request("DELETE", f"/customer/{NO_CONSENT}")
    _request("DELETE", f"/customer/{NO_SCOPE}")

    _section("CONSENT GATE — INGESTION")

    # No consent at all → 403
    status, body = _request("POST", "/events", {
        "customer_id": NO_CONSENT,
        "client_event_id": str(uuid.uuid4()),
        "event_type": "page_view",
        "payload": {"page": "/x"},
    })
    print(f"no-consent customer → POST /events → {status} {body}")
    assert status == 403, "expected 403 for ungated customer"

    # Consent without personalization scope → 403
    _request("POST", "/consent", {"customer_id": NO_SCOPE, "scopes": ["analytics"]})
    status, body = _request("POST", "/events", {
        "customer_id": NO_SCOPE,
        "client_event_id": str(uuid.uuid4()),
        "event_type": "page_view",
        "payload": {"page": "/x"},
    })
    print(f"no-scope customer    → POST /events → {status} {body}")
    assert status == 403, "expected 403 for missing personalization scope"

    _section("HAPPY PATH — INGEST + PROCESS")

    # Set up consent
    _request("POST", "/consent", {
        "customer_id": CUSTOMER,
        "scopes": ["personalization", "analytics"],
        "data_retention_days": 30,
    })
    print(f"created consent for {CUSTOMER}")

    # Ingest 3 events
    job_ids = []
    for i in range(3):
        status, body = _request("POST", "/events", {
            "customer_id": CUSTOMER,
            "client_event_id": str(uuid.uuid4()),
            "event_type": "purchase",
            "payload": {"product": f"item_{i}"},
        })
        assert status == 202, f"expected 202, got {status}"
        job_ids.append(body["job_id"])
    print(f"ingested 3 events → jobs {job_ids}")

    # Wait for the worker to process them. Sequential supervisor takes ~2s/event
    # in mock mode, so we wait long enough for 3 events with headroom.
    print("waiting 10s for worker...")
    time.sleep(10)

    # Spot-check state
    events = dynamodb.Table("customer_events").query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq(f"CUSTOMER#{CUSTOMER}"),
    ).get("Items", [])
    print(f"DynamoDB events: {len(events)} (expect 3)")
    assert len(events) == 3
    has_expires = sum(1 for e in events if e.get("expires_at"))
    print(f"with expires_at: {has_expires} (expect 3, set from retention_days)")
    assert has_expires == 3

    facts = os_client.search(
        index="customer-facts",
        body={"size": 50, "query": {"term": {"customer_id": CUSTOMER}}},
    )["hits"]["hits"]
    print(f"OpenSearch customer-facts:       {len(facts)} (expect ≥3)")
    assert len(facts) >= 3

    behaviors = os_client.search(
        index="behavior-embeddings",
        body={"size": 50, "query": {"term": {"customer_id": CUSTOMER}}},
    )["hits"]["hits"]
    print(f"OpenSearch behavior-embeddings: {len(behaviors)} (expect 3)")
    assert len(behaviors) == 3

    _section("RIGHT-TO-DELETE")

    # Trigger a /recommend so we have an offer cached in Redis
    _request("GET", "/recommend?" + urlencode({"customer_id": CUSTOMER, "context": "shoes"}))
    print("triggered /recommend so cache key exists")

    cache_keys = list(redis_client.scan_iter(match=f"offer:{CUSTOMER}:*"))
    print(f"Redis cache keys before: {len(cache_keys)}")

    status, body = _request("DELETE", f"/customer/{CUSTOMER}")
    print(f"DELETE /customer/{CUSTOMER} → {status}")
    print(f"  {body}")
    assert status == 200

    # Re-verify everything is gone
    events_after = dynamodb.Table("customer_events").query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq(f"CUSTOMER#{CUSTOMER}"),
    ).get("Items", [])
    print(f"DynamoDB events after delete:    {len(events_after)} (expect 0)")
    assert len(events_after) == 0

    consent_resp = dynamodb.Table("customer_consent").get_item(
        Key={"PK": f"CUSTOMER#{CUSTOMER}", "SK": "CONSENT"},
    )
    print(f"DynamoDB consent after delete:   {'present' if consent_resp.get('Item') else 'absent'}")
    assert not consent_resp.get("Item")

    # OpenSearch delete_by_query is async; give it a moment
    time.sleep(1)
    facts_after = os_client.search(
        index="customer-facts",
        body={"size": 50, "query": {"term": {"customer_id": CUSTOMER}}},
    )["hits"]["hits"]
    print(f"OpenSearch facts after delete:   {len(facts_after)} (expect 0)")
    assert len(facts_after) == 0

    cache_after = list(redis_client.scan_iter(match=f"offer:{CUSTOMER}:*"))
    print(f"Redis cache keys after:          {len(cache_after)} (expect 0)")
    assert len(cache_after) == 0

    print()
    print("PASS — all privacy checks passed")


if __name__ == "__main__":
    main()

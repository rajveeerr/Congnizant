"""Right-to-delete (GDPR Art. 17 / "right to erasure").

DELETE /customer/{customer_id} wipes:
  - all events from DynamoDB customer_events
  - the customer's consent record
  - all vectors from OpenSearch (3 collections)
  - Redis keys: session:{id}, profile:{id}:hot, all offer:{id}:* cache entries

Returns counts of what was deleted.
"""

import logging

from fastapi import APIRouter, HTTPException

from shared.constants import (
    COLLECTION_BEHAVIOR,
    COLLECTION_FACTS,
    COLLECTION_SESSIONS,
)

from ..deps import dynamo as _dynamo
from ..deps import redis_client as _redis
from ..deps import vectors as _vectors


log = logging.getLogger(__name__)
router = APIRouter()


def _delete_redis_for_customer(customer_id: str) -> int:
    """Delete every Redis key owned by this customer. Returns count."""
    deleted = 0
    # Direct keys
    for key in [f"session:{customer_id}", f"profile:{customer_id}:hot"]:
        if _redis.delete(key):
            deleted += 1
    # Pattern: offer cache. SCAN to avoid blocking on KEYS in prod.
    for key in _redis.scan_iter(match=f"offer:{customer_id}:*"):
        if _redis.delete(key):
            deleted += 1
    return deleted


def _delete_vectors_for_customer(customer_id: str) -> int:
    """Run delete_by_query on each collection. delete_by_query doesn't
    surface a per-doc count, so we return the number of collections wiped."""
    collections = [COLLECTION_FACTS, COLLECTION_BEHAVIOR, COLLECTION_SESSIONS]
    for c in collections:
        _vectors.delete_by_customer(c, customer_id)
    return len(collections)


@router.delete("/customer/{customer_id}")
def delete_customer(customer_id: str) -> dict:
    log.info("DELETE customer %s", customer_id)

    events_deleted = _dynamo.delete_all_events_for_customer(customer_id)
    consent_deleted = _dynamo.delete_consent(customer_id)
    redis_keys_deleted = _delete_redis_for_customer(customer_id)
    vector_collections = _delete_vectors_for_customer(customer_id)

    if not (events_deleted or consent_deleted or redis_keys_deleted):
        raise HTTPException(
            status_code=404,
            detail="no data found for customer",
        )

    return {
        "customer_id": customer_id,
        "events_deleted": events_deleted,
        "consent_deleted": consent_deleted,
        "redis_keys_deleted": redis_keys_deleted,
        "vector_collections_cleared": vector_collections,
    }

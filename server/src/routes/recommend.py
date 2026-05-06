"""Personalized recommendation endpoint.

Cache check → enqueue generate_recommendation job → wait for the worker
to push a result → cache it → return.
"""

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from shared.dynamo import DynamoClient
from shared.queue import make_redis, pop_result, push_job
from shared.schemas import Job

from ..config import settings
from ..middleware.auth import current_customer_id


log = logging.getLogger(__name__)
router = APIRouter()

_dynamo = DynamoClient(endpoint=settings.dynamodb_endpoint, region=settings.aws_region)
_redis = make_redis(settings.redis_url)

CACHE_TTL_SECONDS = 300
RESULT_TIMEOUT_SECONDS = 30


def _cache_key(customer_id: str, context: str) -> str:
    h = hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]
    return f"offer:{customer_id}:{h}"


@router.get("/recommend")
def recommend(
    context: str = Query(..., min_length=1),
    customer_id: str = Depends(current_customer_id),
) -> dict:
    cache_key = _cache_key(customer_id, context)

    cached = _redis.get(cache_key)
    if cached:
        log.info("recommend cache hit: %s", cache_key)
        result = json.loads(cached)
        result["cached"] = True
        return result

    job = Job(
        job_type="generate_recommendation",
        payload={"customer_id": customer_id, "context": context},
    )
    _dynamo.put_job(job.model_dump())
    push_job(_redis, job.model_dump_json())

    payload = pop_result(_redis, job.job_id, timeout=RESULT_TIMEOUT_SECONDS)
    if payload is None:
        raise HTTPException(
            status_code=504,
            detail=f"recommendation timed out after {RESULT_TIMEOUT_SECONDS}s",
        )

    result = json.loads(payload)
    result["cached"] = False
    _redis.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
    return result

"""Event ingestion — singular and batch.

Both endpoints share `_ingest_events`, which:
  - dedups within the batch by client_event_id (first occurrence wins)
  - reads consent once per unique customer in the batch
  - rejects events for ungated customers with status="rejected"
  - bulk-writes accepted events + jobs to DynamoDB (BatchWriteItem)
  - pipelined LPUSH of all jobs onto the worker queue

Idempotency:
  - event_id = client_event_id (frontend-supplied UUID)
  - job_id   = f"evt_{client_event_id}"  (deterministic from event)
  - Retries overwrite the same DDB rows on PK+SK collision; deterministic
    vector doc_ids in the analyzer keep OpenSearch idempotent too.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from shared.dynamo import DynamoClient
from shared.queue import make_redis, push_jobs
from shared.schemas import (
    CustomerEvent,
    IngestBatchRequest,
    IngestBatchResponse,
    IngestEventRequest,
    IngestEventResult,
    Job,
)

from ..config import settings


router = APIRouter()
_dynamo = DynamoClient(endpoint=settings.dynamodb_endpoint, region=settings.aws_region)
_redis = make_redis(settings.redis_url)


def _ingest_events(reqs: list[IngestEventRequest]) -> IngestBatchResponse:
    # Dedup within the batch — first occurrence wins.
    by_id: dict[str, IngestEventRequest] = {}
    for r in reqs:
        by_id.setdefault(r.client_event_id, r)
    unique = list(by_id.values())

    # One consent read per unique customer in the batch.
    customers = {r.customer_id for r in unique}
    consents: dict[str, dict | None] = {
        cid: _dynamo.get_consent(cid) for cid in customers
    }

    now_epoch = int(datetime.now(timezone.utc).timestamp())

    accepted_events: list[dict] = []
    accepted_jobs: list[dict] = []
    accepted_payloads: list[str] = []
    result_by_id: dict[str, IngestEventResult] = {}

    for r in unique:
        consent = consents.get(r.customer_id)
        if not consent:
            result_by_id[r.client_event_id] = IngestEventResult(
                client_event_id=r.client_event_id,
                status="rejected",
                reason="no_consent_record",
            )
            continue
        if "personalization" not in (consent.get("scopes") or set()):
            result_by_id[r.client_event_id] = IngestEventResult(
                client_event_id=r.client_event_id,
                status="rejected",
                reason="missing_personalization_scope",
            )
            continue

        retention_days = int(consent.get("data_retention_days", 90))
        expires_at = now_epoch + retention_days * 86400

        event = CustomerEvent(
            customer_id=r.customer_id,
            event_id=r.client_event_id,
            event_type=r.event_type,
            payload=r.payload,
            consent_scope=r.consent_scope,
            expires_at=expires_at,
        )
        job = Job(
            job_id=f"evt_{r.client_event_id}",
            job_type="process_event",
            payload={
                "event_id": event.event_id,
                "customer_id": event.customer_id,
            },
        )
        accepted_events.append(event.model_dump())
        accepted_jobs.append(job.model_dump())
        accepted_payloads.append(job.model_dump_json())
        result_by_id[r.client_event_id] = IngestEventResult(
            client_event_id=r.client_event_id,
            status="queued",
            event_id=event.event_id,
            job_id=job.job_id,
        )

    if accepted_events:
        _dynamo.batch_put_events(accepted_events)
        _dynamo.batch_put_jobs(accepted_jobs)
        push_jobs(_redis, accepted_payloads)

    # Preserve original submission order; within-batch dups share the same result.
    final_results = [result_by_id[r.client_event_id] for r in reqs]
    accepted = sum(1 for res in final_results if res.status == "queued")
    rejected = len(final_results) - accepted

    return IngestBatchResponse(
        accepted=accepted,
        rejected=rejected,
        results=final_results,
    )


@router.post("/events", status_code=202)
def ingest_event(req: IngestEventRequest) -> dict:
    """Singular endpoint, kept for backwards compat. Routes through the batch path."""
    response = _ingest_events([req])
    result = response.results[0]
    if result.status == "rejected":
        raise HTTPException(status_code=403, detail=result.reason)
    return {
        "event_id": result.event_id,
        "job_id": result.job_id,
        "status": "queued",
    }


@router.post("/events/batch", status_code=200)
def ingest_event_batch(req: IngestBatchRequest) -> IngestBatchResponse:
    return _ingest_events(req.events)

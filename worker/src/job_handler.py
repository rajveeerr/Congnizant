"""Job dispatcher.

Reads job_type and routes to the matching handler. Wraps the call in
status updates so jobs flip running → completed/failed in DynamoDB no
matter what the handler does. Exceptions are caught and logged — they
mark the job failed but don't kill the worker.

ctx is a dict of shared singletons (dynamo, bedrock, vectors, tracer,
supervisor) constructed once in main.py and passed to every handler.
"""

import json
import logging

from shared.schemas import utc_now_iso

from .handlers import generate_recommendation, process_event

log = logging.getLogger(__name__)

HANDLERS = {
    "process_event": process_event.handle,
    "generate_recommendation": generate_recommendation.handle,
}


def dispatch(payload: str, ctx: dict) -> None:
    dynamo = ctx["dynamo"]

    job = json.loads(payload)
    job_id = job["job_id"]
    job_type = job["job_type"]

    handler = HANDLERS.get(job_type)
    if handler is None:
        log.error("Unknown job_type: %s (job_id=%s)", job_type, job_id)
        dynamo.update_job_status(
            job_id,
            "failed",
            completed_at=utc_now_iso(),
            error=f"unknown job_type: {job_type}",
        )
        return

    log.info("Dispatching job %s (type=%s)", job_id, job_type)
    dynamo.update_job_status(job_id, "running")

    try:
        handler(job, ctx)
    except Exception as e:  # noqa: BLE001
        log.exception("Job %s failed", job_id)
        dynamo.update_job_status(
            job_id,
            "failed",
            completed_at=utc_now_iso(),
            error=f"{type(e).__name__}: {e}",
        )
        return

    dynamo.update_job_status(job_id, "completed", completed_at=utc_now_iso())
    log.info("Job %s completed", job_id)

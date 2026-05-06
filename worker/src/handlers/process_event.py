"""Run the supervisor pipeline for a process_event job.

Reads the event from DynamoDB, flips it through processing → processed,
and delegates the actual work to the supervisor (privacy + analyzer).
"""

import logging

log = logging.getLogger(__name__)


def handle(job: dict, ctx: dict) -> None:
    payload = job["payload"]
    customer_id = payload["customer_id"]
    event_id = payload["event_id"]

    dynamo = ctx["dynamo"]
    supervisor = ctx["supervisor"]

    event = dynamo.get_event(customer_id, event_id)
    if not event:
        raise ValueError(f"event {event_id} not found in DynamoDB")

    log.info("Processing event %s for customer %s", event_id, customer_id)
    dynamo.update_event_status(customer_id, event_id, "processing")

    result = supervisor.run_process_event(job["job_id"], event)

    dynamo.update_event_status(customer_id, event_id, "processed")
    log.info("Event %s done (status=%s)", event_id, result.get("status"))

"""Stub handler for generate_recommendation jobs.

Phase 8 fills this in (calls recommender_tool + verifier_tool, caches
the offer in Redis, writes the result somewhere the server can read).
"""

import logging

log = logging.getLogger(__name__)


def handle(job: dict, ctx: dict) -> None:
    customer_id = job["payload"].get("customer_id", "unknown")
    log.info("Generating recommendation for customer %s (stub)", customer_id)

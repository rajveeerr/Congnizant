"""Privacy gate: consent check + PII redaction.

Phase 11 swaps the regex redactor for AWS Comprehend.
"""

import logging

from shared.dynamo import DynamoClient
from shared.pii import redact

log = logging.getLogger(__name__)


def check_privacy(
    customer_id: str,
    text: str,
    dynamo: DynamoClient,
    required_scope: str = "personalization",
) -> dict:
    """Check consent and redact PII.

    Returns:
        {"allowed": False, "reason": ...}                                   if blocked
        {"allowed": True, "redacted_text": ..., "pii_found": int, ...}      if allowed
    """
    consent = dynamo.get_consent(customer_id)
    if not consent:
        return {"allowed": False, "reason": "no_consent_record"}

    scopes = consent.get("scopes") or set()
    if required_scope not in scopes:
        return {"allowed": False, "reason": f"scope_missing:{required_scope}"}

    redacted_text, entities = redact(text)
    log.info("privacy: cust=%s pii=%d", customer_id, len(entities))
    return {
        "allowed": True,
        "redacted_text": redacted_text,
        "pii_found": len(entities),
        "pii_entities": entities,
    }

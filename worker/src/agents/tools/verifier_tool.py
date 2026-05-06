"""Verifier: chain-of-verification.

Asks Claude whether a draft recommendation is accurate w.r.t. the source
context. If yes, the draft passes through unchanged. If no, Claude
rewrites the recommendation and the rewrite becomes the final offer.
"""

import logging

from shared.bedrock import BedrockClientProtocol

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are a fact-checker. Verify a recommendation draft against the "
    "source data. If the draft accurately reflects the source, reply "
    "with exactly the word VALID. Otherwise rewrite the recommendation "
    "to be accurate. Use only information from the source data."
)


def verify_recommendation(
    draft_offer: str,
    source_context: str,
    bedrock: BedrockClientProtocol,
) -> dict:
    prompt = (
        f"Draft recommendation: {draft_offer}\n\n"
        f"Source data:\n{source_context}\n\n"
        "Reply VALID if accurate, otherwise rewrite the recommendation."
    )
    verdict = bedrock.generate(prompt=prompt, system=_SYSTEM).strip()

    if verdict.upper().startswith("VALID"):
        log.info("verifier: status=valid")
        return {"status": "valid", "final_offer": draft_offer}

    log.info("verifier: status=corrected")
    return {"status": "corrected", "final_offer": verdict}

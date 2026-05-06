"""Recommender: retrieve facts + behaviors, generate a personalized offer.

Phase 7 inserts ACE ranking between retrieval and prompt construction.
For now we just use raw similarity from the vector store.
"""

import logging

from shared.bedrock import BedrockClientProtocol
from shared.constants import COLLECTION_BEHAVIOR, COLLECTION_FACTS
from shared.vector_store import VectorStoreProtocol

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are HyperPersona's recommendation agent. Generate one personalized "
    "offer based ONLY on the provided facts and recent behavior. Do not "
    "invent facts. Be specific and concise."
)


def _build_prompt(facts: list[dict], behaviors: list[dict], context: str) -> str:
    fact_lines = "\n".join(f"- {f['text']}" for f in facts) or "(no facts on file)"
    behav_lines = "\n".join(f"- {b['text']}" for b in behaviors) or "(no recent behavior)"
    return (
        f"Customer context: {context}\n\n"
        f"Known facts about this customer:\n{fact_lines}\n\n"
        f"Recent behavior:\n{behav_lines}\n\n"
        "Write a single 1-sentence personalized offer."
    )


def generate_recommendation(
    customer_id: str,
    context: str,
    bedrock: BedrockClientProtocol,
    vectors: VectorStoreProtocol,
) -> dict:
    query = bedrock.embed(context)

    facts = vectors.search(
        COLLECTION_FACTS, query, k=6, filter_customer=customer_id
    )
    behaviors = vectors.search(
        COLLECTION_BEHAVIOR, query, k=4, filter_customer=customer_id
    )

    prompt = _build_prompt(facts, behaviors, context)
    offer = bedrock.generate(prompt=prompt, system=_SYSTEM)

    log.info(
        "recommender: cust=%s facts=%d behaviors=%d",
        customer_id, len(facts), len(behaviors),
    )
    return {
        "offer": offer,
        "facts_used": len(facts),
        "behaviors_used": len(behaviors),
    }

"""Bedrock client wrapper.

Two implementations of the same shape:
  - BedrockClient        real, talks to AWS  (BEDROCK_MODE=real)
  - MockBedrockClient    deterministic stub  (BEDROCK_MODE=mock)

Use make_bedrock_client(...) to build the right one for the current mode.
The mock returns 1024-dim unit-normalized vectors derived from sha256 of
the input, so the same text always maps to the same vector. Generated
text just echoes back the prompt — enough to verify wiring before AWS
creds are available.

To swap mock → real:
  1. Put real AWS creds in .env
  2. Set BEDROCK_MODE=real in .env
  3. make restart-worker
"""

import hashlib
import json
import logging
import math
from typing import Protocol

log = logging.getLogger(__name__)


class BedrockClientProtocol(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def generate(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str: ...


# --- Real client ---------------------------------------------------------

class BedrockClient:
    """Real AWS Bedrock client. Activated when BEDROCK_MODE=real."""

    def __init__(self, region: str, text_model: str, embed_model: str):
        import boto3  # imported lazily so mock-mode users don't need creds
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.text_model = text_model
        self.embed_model = embed_model

    def embed(self, text: str) -> list[float]:
        response = self.client.invoke_model(
            modelId=self.embed_model,
            body=json.dumps({"inputText": text}),
        )
        return json.loads(response["body"].read())["embedding"]

    def generate(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        body: dict = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if system:
            body["system"] = system
        response = self.client.invoke_model(
            modelId=self.text_model,
            body=json.dumps(body),
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"]


# --- Mock client ---------------------------------------------------------

class MockBedrockClient:
    """Deterministic mock. Same input → same output, every time."""

    EMBED_DIM = 1024

    def embed(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        out: list[float] = []
        counter = 0
        while len(out) < self.EMBED_DIM:
            chunk = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            for byte in chunk:
                out.append((byte - 128) / 128.0)
                if len(out) >= self.EMBED_DIM:
                    break
            counter += 1
        # unit-normalize so cosine math behaves like real Titan output
        mag = math.sqrt(sum(x * x for x in out))
        return [x / mag for x in out] if mag > 0 else out

    def generate(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        prompt_short = prompt.replace("\n", " ")[:120]
        system_short = system.replace("\n", " ")[:60]
        return f"[mock] system='{system_short}' prompt='{prompt_short}' → mock response"


# --- Factory -------------------------------------------------------------

def make_bedrock_client(
    mode: str,
    region: str,
    text_model: str,
    embed_model: str,
) -> BedrockClientProtocol:
    if mode == "mock":
        log.info("BedrockClient: mock mode")
        return MockBedrockClient()
    if mode == "real":
        log.info("BedrockClient: real mode (region=%s, text=%s, embed=%s)",
                 region, text_model, embed_model)
        return BedrockClient(region=region, text_model=text_model, embed_model=embed_model)
    raise ValueError(f"Unknown BEDROCK_MODE: {mode!r} (expected 'mock' or 'real')")

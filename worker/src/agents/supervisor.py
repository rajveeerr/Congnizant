"""Supervisor — manual orchestrator (mock-mode).

Walks an event through the agent pipeline:
  privacy_check  →  (if blocked, stop)
                 →  analyze_behavior

When real Bedrock is available, replace the body of run_process_event
with a Strands Agent invocation that has all four tools registered.
The handler that calls this supervisor doesn't need to change.
"""

import logging
import time
from typing import Any, Callable

from shared.bedrock import BedrockClientProtocol
from shared.dynamo import DynamoClient
from shared.vector_store import VectorStoreProtocol

from ..trace_logger import TraceLogger
from .tools import analyzer_tool, privacy_tool

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(
        self,
        dynamo: DynamoClient,
        bedrock: BedrockClientProtocol,
        vectors: VectorStoreProtocol,
        tracer: TraceLogger,
    ) -> None:
        self.dynamo = dynamo
        self.bedrock = bedrock
        self.vectors = vectors
        self.tracer = tracer

    # ------------------------------------------------------------------

    def run_process_event(self, job_id: str, event: dict) -> dict:
        """Run the privacy → analyzer pipeline for one customer event."""
        customer_id = event["customer_id"]
        event_id = event["event_id"]
        event_text = self._serialize_event(event)

        self.tracer.log(
            job_id, "supervisor", "start",
            {"customer_id": customer_id, "event_id": event_id},
            {"event_text_len": len(event_text)},
            0.0, "ok",
        )

        # 1. Privacy gate
        privacy = self._step(
            job_id, "privacy", "check_privacy",
            {"customer_id": customer_id, "text_len": len(event_text)},
            lambda: privacy_tool.check_privacy(customer_id, event_text, self.dynamo),
        )

        if not privacy.get("allowed"):
            self.tracer.log(
                job_id, "supervisor", "blocked",
                {}, {"reason": privacy.get("reason")},
                0.0, "ok",
            )
            return {"status": "blocked", "reason": privacy.get("reason")}

        # 2. Analyze + store
        analysis = self._step(
            job_id, "analyzer", "analyze_behavior",
            {"customer_id": customer_id, "event_id": event_id,
             "redacted_text_len": len(privacy["redacted_text"])},
            lambda: analyzer_tool.analyze_behavior(
                customer_id,
                privacy["redacted_text"],
                event_id,
                self.bedrock,
                self.vectors,
            ),
        )

        self.tracer.log(
            job_id, "supervisor", "end",
            {}, {"status": "ok", **analysis},
            0.0, "ok",
        )
        return {"status": "ok", **analysis}

    # ------------------------------------------------------------------

    def _step(
        self,
        job_id: str,
        agent_name: str,
        step: str,
        input_data: Any,
        fn: Callable[[], Any],
    ) -> Any:
        """Run a step, time it, log to tracer, re-raise on error."""
        start = time.time()
        try:
            output = fn()
            duration = (time.time() - start) * 1000
            self.tracer.log(job_id, agent_name, step, input_data, output, duration, "ok")
            return output
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.tracer.log(
                job_id, agent_name, step, input_data,
                {"error": f"{type(e).__name__}: {e}"},
                duration, "error",
            )
            raise

    @staticmethod
    def _serialize_event(event: dict) -> str:
        """Render an event dict to plain text for the agent."""
        parts = [f"event_type: {event.get('event_type', 'unknown')}"]
        for k, v in (event.get("payload") or {}).items():
            parts.append(f"{k}: {v}")
        return "; ".join(parts)

import logging

from shared.bedrock import make_bedrock_client
from shared.dynamo import DynamoClient
from shared.queue import make_redis, pop_job
from shared.vector_store import InMemoryVectorStore

from .agents.supervisor import Supervisor
from .config import settings
from .job_handler import dispatch
from .trace_logger import TraceLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("worker")


def main() -> None:
    redis_client = make_redis(settings.redis_url)
    dynamo = DynamoClient(endpoint=settings.dynamodb_endpoint, region=settings.aws_region)
    bedrock = make_bedrock_client(
        mode=settings.bedrock_mode,
        region=settings.bedrock_region,
        text_model=settings.bedrock_text_model,
        embed_model=settings.bedrock_embed_model,
    )
    vectors = InMemoryVectorStore()
    tracer = TraceLogger("/tmp/agent_traces.db")
    supervisor = Supervisor(dynamo=dynamo, bedrock=bedrock, vectors=vectors, tracer=tracer)

    ctx = {
        "dynamo": dynamo,
        "bedrock": bedrock,
        "vectors": vectors,
        "tracer": tracer,
        "supervisor": supervisor,
    }

    redis_client.ping()
    log.info("worker started, waiting for jobs (redis=%s, bedrock=%s)",
             settings.redis_url, settings.bedrock_mode)

    while True:
        try:
            payload = pop_job(redis_client, timeout=0)
            if payload is None:
                continue
            dispatch(payload, ctx)
        except KeyboardInterrupt:
            log.info("worker shutting down")
            break
        except Exception:
            log.exception("worker loop error — continuing")


if __name__ == "__main__":
    main()

"""Per-API-key fixed-window rate limit.

Counter keyed on (api_key, current_minute) in Redis. Increment on every
request; reject with 429 once we cross the limit. The 5-second TTL slop
on the bucket key keeps Redis tidy without affecting accuracy.

Mounted AFTER the API key auth middleware so unauthenticated requests
fail at 401 and never pollute rate-limit buckets.

Bypassed paths: /health, /, /docs, /openapi.json, /redoc, /metrics/queue.
The metrics endpoint specifically must NOT be rate-limited so operators
can always see queue depth even under overload.
"""

import logging
import time

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


log = logging.getLogger(__name__)

_BYPASS_PATHS = {
    "/health", "/", "/docs", "/openapi.json", "/redoc", "/metrics/queue",
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        redis_client,
        limit: int,
        window_s: int = 60,
    ) -> None:
        super().__init__(app)
        self.redis = redis_client
        self.limit = limit
        self.window_s = window_s

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _BYPASS_PATHS:
            return await call_next(request)

        api_key = request.headers.get("x-api-key", "anonymous")
        window = int(time.time() // self.window_s)
        bucket = f"rate:key:{api_key}:{window}"

        count = self.redis.incr(bucket)
        if count == 1:
            self.redis.expire(bucket, self.window_s + 5)

        if count > self.limit:
            retry_after = self.window_s - (int(time.time()) % self.window_s)
            log.warning(
                "rate limit exceeded",
                extra={"api_key": api_key, "count": count, "limit": self.limit},
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "api_key rate limit exceeded",
                    "limit": self.limit,
                    "window_seconds": self.window_s,
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

"""JWT auth middleware.

Decodes the Bearer token on every protected request and stashes the
resolved customer_id on `request.state`. Routes pull it via
`Depends(current_customer_id)`.
"""

import jwt
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..auth import decode_token


PUBLIC_PATHS = {
    "/health",
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/login",
    "/register",
}


class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "missing or malformed Authorization header"},
            )
        token = header.split(None, 1)[1].strip()

        try:
            customer_id = decode_token(token)
        except jwt.PyJWTError as e:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": f"invalid token: {e}"},
            )

        request.state.customer_id = customer_id
        return await call_next(request)


def current_customer_id(request: Request) -> str:
    """FastAPI dependency that returns the auth'd customer_id stamped by
    JWTAuthMiddleware. Routes use this instead of accepting customer_id
    from the client."""
    return request.state.customer_id

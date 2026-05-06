"""Auth primitives — password hashing and JWT issuance.

Used by the /register and /login routes and the JWTAuthMiddleware.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(customer_id: str) -> tuple[str, int]:
    """Returns (jwt, expires_in_seconds)."""
    expires_in = settings.jwt_expiry_hours * 3600
    now = datetime.now(timezone.utc)
    payload = {
        "sub": customer_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_token(token: str) -> str:
    """Returns customer_id. Raises jwt.PyJWTError on invalid/expired token."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise jwt.InvalidTokenError("token missing sub claim")
    return sub

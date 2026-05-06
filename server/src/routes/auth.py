"""Register and login endpoints.

Both are public (see PUBLIC_PATHS in the JWT middleware) and return an
AuthResponse carrying a Bearer token the frontend uses on every other
request.
"""

import logging

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException

from shared.dynamo import DynamoClient
from shared.schemas import (
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    new_uuid,
    utc_now_iso,
)

from ..auth import create_access_token, hash_password, verify_password
from ..config import settings


log = logging.getLogger(__name__)
router = APIRouter()
_dynamo = DynamoClient(endpoint=settings.dynamodb_endpoint, region=settings.aws_region)


def _issue(customer_id: str, email: str) -> AuthResponse:
    token, expires_in = create_access_token(customer_id)
    return AuthResponse(
        customer_id=customer_id,
        email=email,
        token=token,
        expires_in=expires_in,
    )


@router.post("/register")
def register(req: RegisterRequest) -> AuthResponse:
    email = req.email.lower()
    customer_id = new_uuid()
    record = {
        "email": email,
        "customer_id": customer_id,
        "password_hash": hash_password(req.password),
        "created_at": utc_now_iso(),
    }
    try:
        _dynamo.put_auth(record)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail="email already registered")
        raise

    log.info("registered customer", extra={"customer_id": customer_id})
    return _issue(customer_id, email)


@router.post("/login")
def login(req: LoginRequest) -> AuthResponse:
    record = _dynamo.get_auth_by_email(req.email)
    # Single 401 response for both "no such email" and "wrong password" so we
    # don't leak which accounts exist.
    if not record or not verify_password(req.password, record.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="invalid email or password")

    return _issue(record["customer_id"], record["email"])

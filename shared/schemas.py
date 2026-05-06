"""Pydantic models shared between server and worker."""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_uuid() -> str:
    return str(uuid4())


class CustomerEvent(BaseModel):
    customer_id: str
    event_id: str = Field(default_factory=new_uuid)
    event_type: str  # page_view, add_to_cart, purchase, return, search
    payload: dict
    status: str = "pending"  # pending → processing → processed | failed
    consent_scope: set[str] = Field(default_factory=set)
    created_at: str = Field(default_factory=utc_now_iso)
    # Epoch seconds — DynamoDB TTL field. Set by the server from the
    # customer's consent retention_days at ingest time.
    expires_at: int | None = None


class ConsentRecord(BaseModel):
    customer_id: str
    scopes: set[str]  # {"personalization", "analytics", "marketing"}
    data_retention_days: int = 90
    last_updated: str = Field(default_factory=utc_now_iso)


class Job(BaseModel):
    job_id: str = Field(default_factory=new_uuid)
    job_type: str  # process_event, generate_recommendation, batch_import
    payload: dict
    status: str = "queued"  # queued → running → completed | failed
    created_at: str = Field(default_factory=utc_now_iso)
    completed_at: str | None = None
    error: str | None = None


class IngestEventRequest(BaseModel):
    # customer_id is resolved server-side from the JWT (not client-supplied).
    # Frontend-supplied UUID. Same retry → same id → no duplicates downstream.
    client_event_id: str
    event_type: str
    payload: dict
    consent_scope: set[str] = Field(default_factory=set)


class IngestBatchRequest(BaseModel):
    events: list[IngestEventRequest] = Field(min_length=1, max_length=100)


class IngestEventResult(BaseModel):
    client_event_id: str
    status: str  # "queued" | "rejected"
    event_id: str | None = None
    job_id: str | None = None
    reason: str | None = None


class IngestBatchResponse(BaseModel):
    accepted: int
    rejected: int
    results: list[IngestEventResult]


class ConsentUpsertRequest(BaseModel):
    scopes: set[str]
    data_retention_days: int = 90


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    customer_id: str
    email: str
    token: str
    token_type: str = "bearer"
    expires_in: int

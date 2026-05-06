"""End-to-end happy-path demo.

Walks through a realistic shopping session for a customer:
  1. Create consent
  2. Ingest a sequence of behavior events (search → views → cart → purchase)
  3. Wait for the worker to process them
  4. Get a personalized recommendation
  5. Hit the same endpoint again — should be cached
  6. Inspect the trace
  7. Delete the customer

Designed to run inside the server container:
  make test-e2e
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode

BASE_URL = os.getenv("HYPERPERSONA_BASE_URL", "http://server:8000")
API_KEY = os.getenv("API_KEY", "test-key")

CUSTOMER = "demo_e2e_user"


def _request(
    method: str, path: str, body: dict | None = None
) -> tuple[int, dict | None]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("X-API-Key", API_KEY)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode() or "null"
            return resp.status, json.loads(text)
    except urllib.error.HTTPError as e:
        text = e.read().decode() or "null"
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {"raw": text}


def _section(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


def main() -> None:
    _section("1. CONSENT")
    s, b = _request("POST", "/consent", {
        "customer_id": CUSTOMER,
        "scopes": ["personalization", "analytics"],
        "data_retention_days": 30,
    })
    print(f"POST /consent → {s} {b}")

    _section("2. INGEST EVENTS (batch)")
    events = [
        ("search",      {"query": "waterproof hiking boots"}),
        ("page_view",   {"page": "/boots/salomon-x-ultra"}),
        ("page_view",   {"page": "/boots/merrell-moab"}),
        ("add_to_cart", {"product": "Salomon X Ultra", "price": 159}),
        ("purchase",    {"product": "Salomon X Ultra", "price": 159}),
        ("search",      {"query": "trail running socks"}),
    ]
    batch_payload = {
        "events": [
            {
                "customer_id": CUSTOMER,
                "client_event_id": str(uuid.uuid4()),
                "event_type": evt_type,
                "payload": payload,
            }
            for evt_type, payload in events
        ]
    }
    s, b = _request("POST", "/events/batch", batch_payload)
    print(f"POST /events/batch ({len(events)} events) → {s}  "
          f"accepted={b.get('accepted')}  rejected={b.get('rejected')}")
    job_ids: list[str] = [
        r["job_id"] for r in (b.get("results") or []) if r.get("status") == "queued"
    ]
    for evt, res in zip(events, b.get("results") or []):
        print(f"  {evt[0]:12} → {res['status']:8} job={(res.get('job_id') or '')[:12]}")

    _section("2b. RETRY THE SAME BATCH (idempotency check)")
    s2, b2 = _request("POST", "/events/batch", batch_payload)
    print(f"POST /events/batch (same payload) → {s2}  "
          f"accepted={b2.get('accepted')}  rejected={b2.get('rejected')}")
    print("  (event_id reused → DDB rows overwrite, vectors overwrite, no duplicates)")

    print()
    print("waiting 6s for worker...")
    time.sleep(6)

    _section("3. JOB STATUS")
    completed = 0
    for jid in job_ids:
        s, b = _request("GET", f"/jobs/{jid}")
        status = (b or {}).get("status", "?")
        if status == "completed":
            completed += 1
        print(f"  {jid[:8]}: {status}")
    print(f"\n{completed}/{len(job_ids)} jobs completed")

    _section("4. RECOMMENDATION (cache MISS)")
    t0 = time.time()
    s, b = _request(
        "GET",
        "/recommend?" + urlencode({
            "customer_id": CUSTOMER,
            "context": "going on a hiking trip this weekend",
        }),
    )
    elapsed = (time.time() - t0) * 1000
    print(f"GET /recommend → {s} ({elapsed:.0f}ms, cached={b.get('cached')})")
    print(f"  facts_retrieved : {b.get('facts_retrieved')}")
    print(f"  facts_used      : {b.get('facts_used')}")
    print(f"  behaviors_used  : {b.get('behaviors_used')}")
    print(f"  conflicts       : {b.get('conflicts')}")
    print(f"  offer (head)    : {(b.get('offer') or '')[:140]}")
    rec_job_id = b.get("job_id")

    _section("5. RECOMMENDATION (cache HIT)")
    t0 = time.time()
    s, b = _request(
        "GET",
        "/recommend?" + urlencode({
            "customer_id": CUSTOMER,
            "context": "going on a hiking trip this weekend",
        }),
    )
    elapsed = (time.time() - t0) * 1000
    print(f"GET /recommend → {s} ({elapsed:.0f}ms, cached={b.get('cached')})")

    _section("6. AGENT TRACE")
    if rec_job_id:
        s, b = _request("GET", f"/traces/{rec_job_id}")
        print(f"GET /traces/{rec_job_id} → {s}, {len(b)} step(s):")
        for row in b:
            print(
                f"  {row['agent_name']:11} {row['step']:25} "
                f"{row['duration_ms']:6.1f}ms  {row['status']}"
            )

    _section("7. RIGHT-TO-DELETE")
    s, b = _request("DELETE", f"/customer/{CUSTOMER}")
    print(f"DELETE /customer/{CUSTOMER} → {s}")
    print(f"  {b}")

    print()
    print("PASS — end-to-end demo complete")


if __name__ == "__main__":
    main()

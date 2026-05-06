"""Complementary-products demo (Stage 2 of the complement feature).

Walks three cart scenarios end-to-end:
  1. Laptop only          → expect bag/mouse/monitor (tech complements)
  2. Hiking boots only    → expect backpack/socks/water bottle (outdoor)
  3. Mixed cart           → expect a sensible mix of both

Auth: registers a fresh user (random email so re-runs don't collide),
obtains a JWT, posts consent, then queries /recommend/complement with
Bearer auth.

Mock-mode behavior: the LLM returns a stub starting with "[mock]" so
the complement_tool falls back to a heuristic (top-N from a different
subcategory than the cart). Recommendations are still sensible-looking,
but the `used_llm` flag in the response will be false. Real Bedrock
flips it to true.

Usage: make demo-complement
Prereq: make seed-products
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode

BASE_URL = os.getenv("HYPERPERSONA_BASE_URL", "http://server:8000")


def _api(
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict | None]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, method=method, data=data)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
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
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_recs(b: dict) -> None:
    print(f"  cart_resolved        : {b.get('cart_resolved')} of "
          f"{len(b.get('cart_items') or [])}")
    print(f"  candidates_considered: {b.get('candidates_considered')}")
    print(f"  facts_used           : {b.get('facts_used', 0)}  "
          f"(personalization signal — Stage 3)")
    print(f"  used_llm             : {b.get('used_llm')}")
    print(f"  cached               : {b.get('cached')}")
    print(f"  recommendations      : {len(b.get('recommendations') or [])}")
    print()
    for r in b.get("recommendations") or []:
        price = r.get("price", 0)
        print(f"    [{r.get('rank', '?')}] {r.get('name', ''):42} "
              f"${price:>7.2f}  ({r.get('subcategory', '')})")
        print(f"        {r.get('reason', '')[:90]}")


def main() -> None:
    _section("0. REGISTER + LOGIN (fresh user)")
    email = f"demo_complement_{uuid.uuid4().hex[:8]}@example.com"
    s, b = _api("POST", "/register", {"email": email, "password": "hunter22hunter"})
    if s != 200:
        print(f"register → {s} {b}")
        return
    token = b["token"]
    print(f"registered {email}, got JWT (len={len(token)})")

    _section("1. CONSENT")
    s, b = _api("POST", "/consent",
                {"scopes": ["personalization", "analytics"]},
                token=token)
    print(f"POST /consent → {s} {b}")

    # ----- Scenario 1: laptop only ----------------------------------------
    _section("2. SCENARIO 1 — cart: [Dell XPS 15 Laptop]")
    qs = urlencode({"cart_items": "laptop_dell_xps_15"})
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement → {s}")
    _print_recs(b)

    # ----- Scenario 2: hiking boots only ----------------------------------
    _section("3. SCENARIO 2 — cart: [Salomon X Ultra Hiking Boots]")
    qs = urlencode({"cart_items": "boots_salomon_x_ultra"})
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement → {s}")
    _print_recs(b)

    # ----- Scenario 3: mixed cart -----------------------------------------
    _section("4. SCENARIO 3 — cart: [Dell XPS 15, Salomon X Ultra, Sony XM5]")
    qs = urlencode({
        "cart_items": "laptop_dell_xps_15,boots_salomon_x_ultra,headphones_sony_xm5"
    })
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement → {s}")
    _print_recs(b)

    # ----- Scenario 4: unknown product (should still work) ----------------
    _section("5. SCENARIO 4 — cart: [unknown_product_id]  (graceful handling)")
    qs = urlencode({"cart_items": "no_such_product_abc"})
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement → {s}")
    _print_recs(b)

    # ----- Scenario 5: cache hit on repeat ---------------------------------
    _section("6. SCENARIO 5 — repeat scenario 1 → should hit cache (fast)")
    qs = urlencode({"cart_items": "laptop_dell_xps_15"})
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement → {s}  cached={b.get('cached')}")
    print(f"  recommendations      : {len(b.get('recommendations') or [])}")

    # Same items in different order should still hit the cache (sorted hash)
    qs = urlencode({"cart_items": "laptop_dell_xps_15"})  # same single item
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement (3rd call) → {s}  cached={b.get('cached')}")

    # ----- Scenario 6: edge case — empty cart_items value -------------------
    _section("7. SCENARIO 6 — empty cart_items value → expect 400")
    qs = urlencode({"cart_items": "  "})  # whitespace only
    s, b = _api("GET", f"/recommend/complement?{qs}", token=token)
    print(f"GET /recommend/complement → {s} {b}")

    _section("8. CLEANUP")
    s, b = _api("DELETE", "/customer", token=token)
    print(f"DELETE /customer → {s} {b}")

    print()
    print("PASS — complement endpoint working end-to-end")


if __name__ == "__main__":
    main()

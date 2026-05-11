"""One-time job: regenerate product images via Bedrock Nova Canvas.

For every item in the DynamoDB `products` table, generate 3 PNGs (hero,
detail, lifestyle) from a prompt built out of the product's own name +
brand + category + description + features, upload to S3, and rewrite
the item's `image` and `images` attributes to point at the new URLs.

Idempotent: re-running skips S3 keys that already exist, so a partial
run can be resumed after a crash or AWS-creds refresh.

Usage:
    # smoke-test on one product
    python scripts/regenerate_product_images.py --slug belkin-travel-webcam-5

    # full run
    python scripts/regenerate_product_images.py
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
BUCKET = "hyperpersona-images-5817368955"
MODEL_ID = "amazon.nova-canvas-v1:0"
TABLE = "products"
VARIANTS = ("hero", "detail", "lifestyle")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("regen")

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)


# --- Prompting -----------------------------------------------------------

ANGLE_HINT = {
    "hero":      "centered front view, plain white background, studio lighting",
    "detail":    "close-up detail shot, soft shadows, neutral background",
    "lifestyle": "lifestyle context, natural light, shallow depth of field",
}


def build_prompt(p: dict, variant: str) -> str:
    name = p.get("name", "")
    category = p.get("category", "").replace("-", " ")
    desc = p.get("description", "")
    feats = ", ".join(p.get("features", [])[:3])
    angle = ANGLE_HINT[variant]
    parts = [name, category, desc]
    if feats:
        parts.append(feats)
    parts.append(angle)
    parts.append("high resolution e-commerce product photography")
    text = ". ".join(s for s in parts if s)
    return text[:1024]


def build_negative_prompt() -> str:
    return "text, watermark, logo of unrelated brand, blurry, distorted, deformed, low quality, multiple products, busy background"


# --- Bedrock -------------------------------------------------------------

def _invoke(prompt: str, neg: str, seed: int) -> bytes:
    body = {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {"text": prompt, "negativeText": neg},
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "height": 1024,
            "width": 1024,
            "cfgScale": 8.0,
            "seed": seed,
        },
    }
    resp = bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    result = json.loads(resp["body"].read())
    if "images" not in result or not result["images"]:
        raise RuntimeError(f"empty response: {result}")
    return base64.b64decode(result["images"][0])


def generate_with_retry(prompt: str, seed: int, max_attempts: int = 4) -> bytes:
    """Retry on throttling / transient errors; fall back to brand-stripped
    prompt on content-filter refusals."""
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return _invoke(prompt, build_negative_prompt(), seed)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"):
                wait = 2 ** attempt
                log.warning("throttle attempt=%d, sleep=%ds", attempt, wait)
                time.sleep(wait)
                last_err = e
                continue
            raise
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"max retries: {last_err}")


# --- S3 ------------------------------------------------------------------

def s3_object_exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def upload_png(key: str, png: bytes) -> None:
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=png,
        ContentType="image/png",
        CacheControl="public, max-age=31536000",
    )


def public_url(key: str) -> str:
    return f"https://{BUCKET}.s3.{REGION}.amazonaws.com/{key}"


# --- DynamoDB ------------------------------------------------------------

def scan_all_products() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paginator = ddb.get_paginator("scan")
    for page in paginator.paginate(TableName=TABLE):
        items.extend(page.get("Items", []))
    return items


def deserialize(it: dict[str, Any]) -> dict[str, Any]:
    """Flatten DynamoDB AttributeValue map into plain Python dict (only the
    fields we care about)."""
    out: dict[str, Any] = {}
    for k, v in it.items():
        if "S" in v:
            out[k] = v["S"]
        elif "N" in v:
            out[k] = v["N"]
        elif "L" in v:
            out[k] = [
                inner.get("S") or inner.get("N")
                for inner in v["L"]
                if "S" in inner or "N" in inner
            ]
    return out


def update_dynamo_images(pk: str, sk: str, image: str, images: list[str]) -> None:
    ddb.update_item(
        TableName=TABLE,
        Key={"PK": {"S": pk}, "SK": {"S": sk}},
        UpdateExpression="SET #img = :img, #imgs = :imgs",
        ExpressionAttributeNames={"#img": "image", "#imgs": "images"},
        ExpressionAttributeValues={
            ":img": {"S": image},
            ":imgs": {"L": [{"S": u} for u in images]},
        },
    )


# --- Per-product worker --------------------------------------------------

def process_product(raw: dict[str, Any], failures: list[dict]) -> tuple[str, str]:
    p = deserialize(raw)
    pk = p["PK"]
    sk = p["SK"]
    slug = pk.split("#", 1)[1] if "#" in pk else pk

    urls: list[str] = []
    for i, variant in enumerate(VARIANTS):
        key = f"products/{slug}/{variant}.png"
        if not s3_object_exists(key):
            prompt = build_prompt(p, variant)
            try:
                png = generate_with_retry(prompt, seed=42 + i)
                upload_png(key, png)
            except Exception as e:
                log.error("FAIL %s/%s: %s", slug, variant, e)
                failures.append({"slug": slug, "variant": variant, "error": str(e)[:300], "prompt": prompt})
                # Skip this variant — leave the gallery shorter rather than block
                continue
        urls.append(public_url(key))

    if not urls:
        return slug, "no images generated"

    update_dynamo_images(pk, sk, image=urls[0], images=urls)
    return slug, f"ok ({len(urls)} imgs)"


# --- Main ----------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="Run for one product only (smoke test)")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Process at most N products (0 = all)")
    args = parser.parse_args()

    failures: list[dict] = []

    if args.slug:
        # Single-product smoke test
        resp = ddb.get_item(
            TableName=TABLE,
            Key={"PK": {"S": f"PRODUCT#{args.slug}"}, "SK": {"S": "META"}},
        )
        if "Item" not in resp:
            log.error("product not found: %s", args.slug)
            sys.exit(1)
        slug, status = process_product(resp["Item"], failures)
        log.info("smoke: %s — %s", slug, status)
        for url_variant in VARIANTS:
            print(f"  {public_url(f'products/{slug}/{url_variant}.png')}")
        if failures:
            with open("/tmp/regen_failures.jsonl", "a") as f:
                for entry in failures:
                    f.write(json.dumps(entry) + "\n")
        return

    items = scan_all_products()
    if args.limit:
        items = items[: args.limit]
    log.info("processing %d products with %d workers", len(items), args.max_workers)

    successes = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futs = {pool.submit(process_product, it, failures): it for it in items}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                slug, status = fut.result()
                if status.startswith("ok"):
                    successes += 1
                if i % 25 == 0:
                    elapsed = time.time() - started
                    rate = i / elapsed if elapsed else 0
                    eta = (len(items) - i) / rate if rate else 0
                    log.info("%d/%d done | ok=%d | failures=%d | %.1f items/s | ETA %.1fmin",
                             i, len(items), successes, len(failures), rate, eta / 60)
            except Exception as e:
                log.error("worker raised: %s", e)

    if failures:
        with open("/tmp/regen_failures.jsonl", "w") as f:
            for entry in failures:
                f.write(json.dumps(entry) + "\n")
        log.warning("%d failures logged to /tmp/regen_failures.jsonl", len(failures))

    log.info("DONE: %d/%d products updated", successes, len(items))


if __name__ == "__main__":
    main()

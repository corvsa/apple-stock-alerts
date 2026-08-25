#!/usr/bin/env python3
"""
Apple Store pickup-stock checker.

Polls Apple's (unofficial, reverse-engineered) fulfillment-messages endpoint
for a fixed list of Mac configurations and posts a push notification to an
ntfy.sh topic whenever a store near the configured ZIP code newly reports
the item as available for pickup.

Designed to be run on a schedule (see .github/workflows/check-stock.yml),
with state persisted to state.json between runs so we only notify on a
genuine unavailable -> available transition (not every single run).
"""

import json
import os
import random
import sys
import time
from pathlib import Path

import requests  # only used for the ntfy notification POST

# Apple's fulfillment-messages endpoint sits behind Akamai bot-management,
# which fingerprints the TLS/HTTP2 handshake itself (JA3/JA4), not just
# headers. Python's `requests` (built on urllib3/OpenSSL) has a fingerprint
# that doesn't match any real browser, so Akamai blocks it outright -- that's
# the "541 Server Error: Unknown" you'll see if you swap this back to
# `requests.get(...)`. curl_cffi wraps libcurl-impersonate, which replays an
# actual Chrome TLS fingerprint byte-for-byte, so the request passes as a
# real browser. See requirements.txt.
from curl_cffi import requests as cffi_requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Where to check for in-store pickup. 94402 = San Mateo, CA (change as needed).
ZIP_CODE = os.environ.get("STOCK_ZIP", "94402")

# ntfy.sh topic to POST notifications to. No auth needed for a public topic,
# but anyone who knows the topic name can read it -- pick an unguessable one
# if that matters to you. Can be overridden via env var for testing.
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh/apple-stock-94402-alerts-2")

STATE_FILE = Path(__file__).parent / "state.json"

# Which browser TLS fingerprint curl_cffi should impersonate.
IMPERSONATE = "chrome124"

# Realistic browser headers to layer on top of the TLS impersonation.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Origin": "https://www.apple.com",
}

FULFILLMENT_URL = "https://www.apple.com/shop/fulfillment-messages"

# Each entry is one specific configuration to watch.
#   part_number: the value sent as parts.0
#   options:     comma-separated BTO option codes sent as option.0 (None for
#                standard, off-the-shelf SKUs that don't need them)
#   referer:     the actual apple.com configurator page for this exact
#                config -- Akamai treats a fulfillment-messages request
#                without a matching Referer as more bot-like, so we send the
#                real one for each product.
PRODUCTS = [
    {
        "name": "Mac mini (M4 / 24GB / 512GB)",
        "part_number": "MCYT4LL/A",
        "options": None,
        "referer": "https://www.apple.com/shop/buy-mac/mac-mini",
    },
    {
        "name": 'MacBook Air 15" Midnight (M5 / 24GB / 512GB)',
        "part_number": "RO_MBA_M5_15_INCH_MIDNIGHT_BET_BES_ULT_2026",
        "options": "065-CKQP,065-CLKK,065-CKN0,065-CKP0,065-CKNY,065-CKP1,065-CKN2,065-CKQW,065-CKMX,065-CKP2",
        "referer": "https://www.apple.com/shop/buy-mac/macbook-air/15-inch-midnight-m5-chip-10-core-cpu-10-core-gpu-24gb-memory-512gb-storage",
    },
    {
        "name": 'MacBook Air 15" Silver (M5 / 24GB / 512GB)',
        "part_number": "RO_MBA_M5_15_INCH_SILVER_BET_BES_ULT_2026",
        "options": "065-CKQM,065-CLKK,065-CKN0,065-CKP0,065-CKNY,065-CKP1,065-CKN2,065-CKQT,065-CKMX,065-CKP2",
        "referer": "https://www.apple.com/shop/buy-mac/macbook-air/15-inch-silver-m5-chip-10-core-cpu-10-core-gpu-24gb-memory-512gb-storage",
    },
    {
        "name": 'MacBook Air 15" Starlight (M5 / 24GB / 512GB)',
        "part_number": "RO_MBA_M5_15_INCH_STARLIGHT_BET_BES_ULT_2026",
        "options": "065-CKQN,065-CLKK,065-CKN0,065-CKP0,065-CKNY,065-CKP1,065-CKN2,065-CKQV,065-CKMX,065-CKP2",
        "referer": "https://www.apple.com/shop/buy-mac/macbook-air/15-inch-starlight-m5-chip-10-core-cpu-10-core-gpu-24gb-memory-512gb-storage",
    },
    {
        "name": 'MacBook Air 15" Sky Blue (M5 / 24GB / 512GB)',
        "part_number": "RO_MBA_M5_15_INCH_SKY_BLUE_BET_BES_ULT_2026",
        "options": "065-CKQQ,065-CLKK,065-CKN0,065-CKP0,065-CKNY,065-CKP1,065-CKN2,065-CKQX,065-CKMX,065-CKP2",
        "referer": "https://www.apple.com/shop/buy-mac/macbook-air/15-inch-sky-blue-m5-chip-10-core-cpu-10-core-gpu-24gb-memory-512gb-storage",
    },
]


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            print(f"WARNING: {STATE_FILE} was corrupt, starting fresh", file=sys.stderr)
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Apple fulfillment-messages API
# ---------------------------------------------------------------------------

def fetch_availability(product, retries=3):
    params = {
        "fae": "true",
        "searchNearby": "true",
        "location": ZIP_CODE,
        "parts.0": product["part_number"],
    }
    if product["options"]:
        params["option.0"] = product["options"]

    headers = dict(HEADERS)
    headers["Referer"] = product["referer"]

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = cffi_requests.get(
                FULFILLMENT_URL,
                params=params,
                headers=headers,
                impersonate=IMPERSONATE,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                wait = attempt * random.uniform(3, 6)
                print(
                    f"  attempt {attempt}/{retries} failed ({exc}); retrying in {wait:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
    else:
        raise last_exc

    stores = (
        data.get("body", {})
        .get("content", {})
        .get("pickupMessage", {})
        .get("stores", [])
    )

    results = []
    for store in stores:
        part_info = store.get("partsAvailability", {}).get(product["part_number"], {})
        results.append(
            {
                "store_name": store.get("storeName", "Unknown store"),
                "store_number": store.get("storeNumber"),
                "city": store.get("city"),
                "state": store.get("state"),
                "pickup_display": part_info.get("pickupDisplay", "unknown"),  # "available" | "unavailable"
                "pickup_quote": part_info.get("pickupSearchQuote", ""),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(title, message, tags="tada,shopping_cart", priority="high"):
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Tags": tags,
                "Priority": priority,
            },
            timeout=15,
        )
        print(f"Notification sent: {title}")
    except requests.RequestException as exc:
        print(f"WARNING: failed to send notification: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    state = load_state()
    any_error = False

    for product in PRODUCTS:
        key = product["part_number"]
        prev = state.get(key, {})
        current = {}

        try:
            availability = fetch_availability(product)
        except Exception as exc:  # noqa: BLE001 - keep the whole run alive
            print(f"ERROR checking {product['name']}: {exc}", file=sys.stderr)
            any_error = True
            continue

        if not availability:
            print(f"{product['name']}: no store results returned (may be online-only or unsupported ZIP)")

        for store in availability:
            store_key = str(store["store_number"] or store["store_name"])
            is_available = store["pickup_display"] == "available"
            current[store_key] = "available" if is_available else "unavailable"

            was_available = prev.get(store_key) == "available"

            status_line = (
                f"{product['name']} @ {store['store_name']}: "
                f"{store['pickup_display']} ({store['pickup_quote'] or 'n/a'})"
            )
            print(status_line)

            if is_available and not was_available:
                notify(
                    title=f"In stock: {product['name']}",
                    message=(
                        f"{store['store_name']} ({store['city']}, {store['state']}) "
                        f"now shows pickup available.\n{store['pickup_quote']}"
                    ),
                )

        state[key] = current
        # Be polite to Apple's servers between requests.
        time.sleep(random.uniform(1.5, 3.5))

    save_state(state)

    if any_error:
        sys.exit(1)


if __name__ == "__main__":
    main()

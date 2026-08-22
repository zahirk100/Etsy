"""Fix the classic dropshipping checkout trap: Shopify tracks inventory by
default, on-hand stock is 0 because the supplier holds the real stock (not
you), and Shopify blocks checkout on a 0-stock variant.

First attempt was just flipping `inventory_policy` to "continue" (allow
selling past zero) — but on this store that got silently reverted, almost
certainly by the newly-connected Facebook & Instagram sales channel
re-syncing inventory. Setting a policy while tracking stays on leaves it
exposed to the next sync doing the same thing again.

The robust fix is to disable Shopify-managed inventory tracking entirely
(`inventory_management = null`) on every active variant — with tracking
off there's no stock count left for any sync to reset, so nothing can
re-trigger "sold out" again.

Real write against the store, gated by config.SHOPIFY_LIVE like other
Shopify writes.
"""
import logging

import requests

from dropship_bot import config

log = logging.getLogger(__name__)


def _get(path: str, params: dict | None = None) -> dict:
    url = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    resp = requests.get(
        url,
        params=params or {},
        headers={"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _put(path: str, payload: dict) -> dict:
    url = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    resp = requests.put(
        url,
        json=payload,
        headers={"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def diagnose() -> list[dict]:
    """Read-only: which active variants still have Shopify inventory
    tracking on (and are therefore exposed to any sync app resetting stock
    to 0 and blocking checkout again)."""
    products = _get("products.json", {"limit": 250, "status": "active"})["products"]
    rows = []
    for p in products:
        for v in p.get("variants", []):
            rows.append(
                {
                    "product_title": p["title"],
                    "variant_id": v["id"],
                    "inventory_quantity": v.get("inventory_quantity", 0),
                    "inventory_management": v.get("inventory_management"),
                    "tracking_enabled": v.get("inventory_management") is not None,
                }
            )
    return rows


def ensure_inventory_not_tracked_everywhere() -> list[dict]:
    """Disables inventory tracking on every active variant that still has it
    on. Returns the variants that were (or, in test-mode, would be) changed.
    """
    changed = []
    for row in diagnose():
        if not row["tracking_enabled"]:
            continue
        if not config.SHOPIFY_LIVE:
            log.info(
                "[TEST MODE] Would disable inventory tracking on variant %s (%s)",
                row["variant_id"],
                row["product_title"],
            )
        else:
            _put(
                f"variants/{row['variant_id']}.json",
                {"variant": {"id": row["variant_id"], "inventory_management": None}},
            )
        changed.append(row)
    return changed

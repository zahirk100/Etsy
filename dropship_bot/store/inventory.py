"""Fix the classic dropshipping checkout trap: Shopify tracks inventory by
default, on-hand stock is 0 because the supplier holds the real stock (not
you), and Shopify blocks checkout on a 0-stock variant unless its
`inventory_policy` is set to "continue" (allow selling past zero). This
ensures every active product's variants have that set, so checkout never
blocks on a stock count that was never meant to be tracked in the first
place.

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
    """Read-only: which variants would currently block checkout at 0 stock."""
    products = _get("products.json", {"limit": 250, "status": "active"})["products"]
    rows = []
    for p in products:
        for v in p.get("variants", []):
            rows.append(
                {
                    "product_title": p["title"],
                    "variant_id": v["id"],
                    "inventory_quantity": v.get("inventory_quantity", 0),
                    "inventory_policy": v.get("inventory_policy", "deny"),
                    "blocks_checkout_at_zero_stock": v.get("inventory_policy") != "continue"
                    and v.get("inventory_quantity", 0) <= 0,
                }
            )
    return rows


def ensure_continue_selling_everywhere() -> list[dict]:
    """Sets inventory_policy='continue' on every variant that would
    otherwise block checkout at 0 stock. Returns the variants that were (or,
    in test-mode, would be) changed.
    """
    changed = []
    for row in diagnose():
        if not row["blocks_checkout_at_zero_stock"]:
            continue
        if not config.SHOPIFY_LIVE:
            log.info(
                "[TEST MODE] Would set inventory_policy=continue on variant %s (%s)",
                row["variant_id"],
                row["product_title"],
            )
        else:
            _put(
                f"variants/{row['variant_id']}.json",
                {"variant": {"id": row["variant_id"], "inventory_policy": "continue"}},
            )
        changed.append(row)
    return changed

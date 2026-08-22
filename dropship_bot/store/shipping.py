"""Ensure every shipping zone has a rate — a zone with none configured can
block checkout entirely. Sets flat free shipping (the standard choice for
cold paid-social traffic: 'free shipping' converts noticeably better than a
line-item shipping fee at checkout).

Real write against the store, gated by TEST_MODE like other store writes.
"""
import logging

import requests

from dropship_bot import config

log = logging.getLogger(__name__)


def _get(path: str) -> dict:
    url = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    resp = requests.get(url, headers={"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, payload: dict) -> dict:
    url = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    resp = requests.post(
        url,
        json=payload,
        headers={"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def ensure_free_shipping_everywhere() -> list[str]:
    """Returns a list of human-readable actions taken (or that would be
    taken, in TEST_MODE)."""
    zones = _get("shipping_zones.json")["shipping_zones"]
    actions = []
    for zone in zones:
        has_any_rate = bool(
            zone.get("price_based_shipping_rates")
            or zone.get("weight_based_shipping_rates")
            or zone.get("carrier_shipping_rate_providers")
        )
        if has_any_rate:
            actions.append(f"{zone['name']}: already has a rate configured, left as-is")
            continue

        if not config.SHOPIFY_LIVE:
            actions.append(f"[TEST MODE] Would add free shipping rate to zone '{zone['name']}'")
            continue

        _post(
            f"shipping_zones/{zone['id']}/price_based_shipping_rates.json",
            {"price_based_shipping_rate": {"name": "Free Shipping", "price": "0.00"}},
        )
        actions.append(f"{zone['name']}: added free shipping rate")
    return actions

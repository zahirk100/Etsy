"""AliExpress product research.

Two supported sources, tried in this order:

1. **Live Affiliate API** (free, via portals.aliexpress.com) — used once
   ALIEXPRESS_APP_KEY/SECRET/TRACKING_ID are set. Uses the standard TOP
   (Taobao Open Platform) MD5 signature scheme shared across AliExpress
   Open Platform APIs. The exact response field names are best-effort from
   the public docs and should be verified against a real call the first
   time you go live — print `raw` from `_call` if products come back empty
   and adjust the parsing in `_parse_hotproducts`.

2. **CSV import** — for when you haven't gone through Affiliate API
   approval yet, or prefer to hand-pick products via AliExpress's own free
   "Dropshipping Center" / DSers and just feed the picks in. Zero setup,
   zero approval wait.

AliExpress has no public API for *placing* dropship orders as an
individual — order fulfillment is handled separately by the free DSers
Shopify app, not by this module.
"""
import csv
import hashlib
import logging
import time
from pathlib import Path

import requests

from dropship_bot import config
from dropship_bot.models import Product

log = logging.getLogger(__name__)

_API_URL = "https://api-sg.aliexpress.com/sync"


def _sign(params: dict, secret: str) -> str:
    ordered = sorted(params.items())
    concatenated = "".join(f"{k}{v}" for k, v in ordered)
    raw = f"{secret}{concatenated}{secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def _call(method: str, business_params: dict) -> dict:
    params = {
        "app_key": config.ALIEXPRESS_APP_KEY,
        "method": method,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sign_method": "md5",
        "v": "2.0",
        "format": "json",
        **business_params,
    }
    params["sign"] = _sign(params, config.ALIEXPRESS_APP_SECRET)
    resp = requests.get(_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_hotproducts(raw: dict) -> list[dict]:
    try:
        products = raw["aliexpress_affiliate_hotproduct_query_response"]["resp_result"][
            "result"
        ]["products"]["product"]
    except KeyError:
        log.warning("Unexpected AliExpress API response shape, got: %s", raw)
        return []

    parsed = []
    for p in products:
        try:
            cost = float(p["target_sale_price"])
        except (KeyError, ValueError, TypeError):
            continue
        parsed.append(
            {
                "supplier_id": str(p.get("product_id", "")),
                "title": p.get("product_title", "Untitled product"),
                "description": p.get("product_title", ""),
                "supplier_cost_usd": cost,
                # Simple starting markup; the ranking/pricing strategy can
                # be refined later without touching this fetch logic.
                "sale_price_usd": round(cost * 3, 2),
                "trend_score": min(100, float(p.get("lastest_volume", 0)) / 10),
                "competition_score": 50,  # AliExpress doesn't expose this; neutral default
                "image_urls": [p["product_main_image_url"]] if p.get("product_main_image_url") else [],
            }
        )
    return parsed


def fetch_via_affiliate_api(keywords: str = "", page_size: int = 20) -> list[dict]:
    raw = _call(
        "aliexpress.affiliate.hotproduct.query",
        {
            "keywords": keywords,
            "page_size": page_size,
            "page_no": 1,
            "target_currency": "USD",
            "target_language": "EN",
            "tracking_id": config.ALIEXPRESS_TRACKING_ID,
            "sort": "LAST_VOLUME_DESC",
        },
    )
    return _parse_hotproducts(raw)


def fetch_via_csv(path: str) -> list[dict]:
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(
            f"ALIEXPRESS_CSV_PATH is set to '{path}' but that file doesn't exist. "
            "Create it with columns: supplier_id,title,description,supplier_cost_usd,"
            "sale_price_usd,trend_score,competition_score,image_urls"
        )
    rows = []
    with file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "supplier_id": row["supplier_id"],
                    "title": row["title"],
                    "description": row["description"],
                    "supplier_cost_usd": float(row["supplier_cost_usd"]),
                    "sale_price_usd": float(row["sale_price_usd"]),
                    "trend_score": float(row["trend_score"]),
                    "competition_score": float(row["competition_score"]),
                    "image_urls": [u for u in row.get("image_urls", "").split(";") if u],
                }
            )
    return rows


def fetch_catalog() -> list[dict]:
    if config.ALIEXPRESS_APP_KEY and config.ALIEXPRESS_APP_SECRET:
        log.info(
            "Fetching product catalog from AliExpress Affiliate API%s",
            f" (keywords: {config.ALIEXPRESS_SEARCH_KEYWORDS!r})" if config.ALIEXPRESS_SEARCH_KEYWORDS else "",
        )
        return fetch_via_affiliate_api(keywords=config.ALIEXPRESS_SEARCH_KEYWORDS)
    if config.ALIEXPRESS_CSV_PATH:
        log.info("Loading product catalog from CSV: %s", config.ALIEXPRESS_CSV_PATH)
        return fetch_via_csv(config.ALIEXPRESS_CSV_PATH)
    raise RuntimeError(
        "No AliExpress source configured. Set ALIEXPRESS_APP_KEY/SECRET (live API) "
        "or ALIEXPRESS_CSV_PATH (manual picks) in .env."
    )


def find_winning_products(top_n: int = 3, min_margin_usd: float = 8.0) -> list[Product]:
    candidates = [Product(**item) for item in fetch_catalog()]
    viable = [p for p in candidates if p.margin_usd >= min_margin_usd]
    ranked = sorted(viable, key=lambda p: p.opportunity_score, reverse=True)
    return ranked[:top_n]

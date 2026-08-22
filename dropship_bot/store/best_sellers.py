"""Select which of the store's existing products to run ads for.

Read-only against the real Shopify store regardless of TEST_MODE (same
reasoning as store.inspect) — this only looks at data that already exists,
it doesn't push anything or spend money.

Ranks by actual order history (units + revenue, last 90 days) when there is
any. A store that hasn't driven traffic yet will have no order history at
all — in that case this falls back to a simple heuristic: price points
around ~25 (in the store's currency) tend to work best for cold Facebook
traffic, since they're cheap enough for an easy purchase decision but leave
enough margin after ad spend.
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from dropship_bot import config
from dropship_bot.models import Product, ShopifyListing

log = logging.getLogger(__name__)

_SWEET_SPOT_PRICE = 25.0


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


def _fetch_active_products() -> list[dict]:
    return _get("products.json", {"limit": 250, "status": "active"})["products"]


def _fetch_recent_orders(days: int = 90) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _get("orders.json", {"status": "any", "created_at_min": cutoff, "limit": 250})["orders"]


def _sales_by_product(orders: list[dict]) -> dict[int, dict]:
    stats: dict[int, dict] = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    for order in orders:
        for item in order.get("line_items", []):
            pid = item.get("product_id")
            if pid is None:
                continue
            stats[pid]["units"] += item.get("quantity", 0)
            stats[pid]["revenue"] += float(item.get("price", 0)) * item.get("quantity", 0)
    return stats


def pick_products_to_advertise(
    top_n: int = 3, exclude_supplier_ids: set[str] | None = None
) -> list[tuple[Product, ShopifyListing]]:
    exclude_supplier_ids = exclude_supplier_ids or set()
    products = [p for p in _fetch_active_products() if str(p["id"]) not in exclude_supplier_ids]
    sales = _sales_by_product(_fetch_recent_orders())
    has_sales_data = any(s["units"] > 0 for s in sales.values())

    def rank_key(p: dict):
        if has_sales_data:
            s = sales.get(p["id"], {"units": 0, "revenue": 0.0})
            return (s["revenue"], s["units"])
        price = float(p["variants"][0]["price"]) if p.get("variants") else 0.0
        return -abs(price - _SWEET_SPOT_PRICE)

    # Higher rank_key is always better in both branches (higher revenue, or
    # closer to the sweet-spot price) — sort descending either way.
    ranked = sorted(products, key=rank_key, reverse=True)

    if has_sales_data:
        log.info("Ranking existing products by actual sales (last 90 days)")
    else:
        log.info(
            "No order history yet — ranking by price-point heuristic "
            "(closest to %.0f, the cold-traffic sweet spot)",
            _SWEET_SPOT_PRICE,
        )

    results = []
    for p in ranked[:top_n]:
        price = float(p["variants"][0]["price"]) if p.get("variants") else 0.0
        product = Product(
            supplier_id=str(p["id"]),
            title=p["title"],
            description=p.get("body_html") or p["title"],
            # Real supplier cost is unknown for products already in the
            # store; this rough estimate only feeds trend/opportunity
            # scoring elsewhere, it's never used to set the live price.
            supplier_cost_usd=round(price / 3, 2),
            sale_price_usd=price,
            trend_score=50,
            competition_score=50,
            image_urls=[img["src"] for img in p.get("images", [])],
        )
        listing = ShopifyListing(
            product=product,
            shopify_product_id=str(p["id"]),
            product_url=f"https://{config.SHOPIFY_STORE_DOMAIN}/products/{p['handle']}",
        )
        results.append((product, listing))
    return results

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

_SWEET_SPOT_PRICE = config.SWEET_SPOT_PRICE


def _get_all(path: str, key: str, params: dict | None = None, max_pages: int = 20) -> list[dict]:
    """GET every page of a REST list endpoint. Shopify caps a page at 250
    and paginates with a `Link: <...page_info=...>; rel="next"` header --
    without following it, stores with >250 products (Elora Moda has ~470)
    only ever had their first 250 considered."""
    url = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    headers = {"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN}
    items: list[dict] = []
    next_params: dict | None = params or {}
    for _ in range(max_pages):
        resp = requests.get(url, params=next_params, headers=headers, timeout=30)
        resp.raise_for_status()
        items += resp.json().get(key, [])
        nxt = resp.links.get("next", {}).get("url")
        if not nxt:
            break
        url, next_params = nxt, None  # the next URL already carries page_info + limit
    return items


def _fetch_active_products() -> list[dict]:
    return _get_all("products.json", "products", {"limit": 250, "status": "active"})


def _fetch_recent_orders(days: int = 90) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return _get_all(
        "orders.json",
        "orders",
        {"status": "any", "created_at_min": cutoff, "limit": 250, "fields": "id,line_items"},
    )


def _is_advertisable(p: dict) -> bool:
    """Only spend money on products a buyer can actually check out, with a
    photo to put in the ad."""
    if not p.get("images") or not p.get("variants"):
        return False
    for v in p["variants"]:
        sold_out = (
            v.get("inventory_management") == "shopify"
            and v.get("inventory_policy") == "deny"
            and (v.get("inventory_quantity") or 0) <= 0
        )
        if not sold_out:
            return True
    return False


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
    products = [
        p
        for p in _fetch_active_products()
        if str(p["id"]) not in exclude_supplier_ids and _is_advertisable(p)
    ]
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
            product_url=f"https://{config.SHOPIFY_PUBLIC_DOMAIN}/products/{p['handle']}",
        )
        results.append((product, listing))
    return results

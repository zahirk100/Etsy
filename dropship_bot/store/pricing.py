"""Pricing review and repricing for existing store products.

Review is read-only and safe to run any time (same reasoning as
store.inspect / store.best_sellers). Applying new prices/costs is a real
write against the live store, gated by config.SHOPIFY_LIVE like other
Shopify writes: without it, apply_pricing/apply_cost only log what they
would change.
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


def fetch_pricing_overview() -> list[dict]:
    """One row per active product's first variant: price, cost, margin."""
    products = _get("products.json", {"limit": 250, "status": "active"})["products"]

    inventory_item_ids = []
    variant_by_item_id = {}
    for p in products:
        if not p.get("variants"):
            continue
        v = p["variants"][0]
        item_id = v.get("inventory_item_id")
        if item_id:
            inventory_item_ids.append(item_id)
            variant_by_item_id[item_id] = (p, v)

    costs_by_item_id = {}
    # Shopify accepts up to 100 ids per call.
    for i in range(0, len(inventory_item_ids), 100):
        batch = inventory_item_ids[i : i + 100]
        data = _get("inventory_items.json", {"ids": ",".join(str(x) for x in batch)})
        for item in data["inventory_items"]:
            costs_by_item_id[item["id"]] = item.get("cost")

    rows = []
    for item_id, (p, v) in variant_by_item_id.items():
        price = float(v["price"])
        cost_raw = costs_by_item_id.get(item_id)
        cost = float(cost_raw) if cost_raw not in (None, "") else None
        margin_pct = round((price - cost) / price * 100, 1) if cost and price else None
        rows.append(
            {
                "product_id": p["id"],
                "variant_id": v["id"],
                "inventory_item_id": item_id,
                "title": p["title"],
                "price": price,
                "cost": cost,
                "margin_pct": margin_pct,
            }
        )
    return sorted(rows, key=lambda r: (r["margin_pct"] is None, r["margin_pct"] or 0))


def print_pricing_overview(rows: list[dict], currency: str = "EUR") -> None:
    print(f"{'Product':45} {'Price':>10} {'Cost':>10} {'Margin':>8}")
    for r in rows:
        cost_str = f"{r['cost']:.2f}" if r["cost"] is not None else "?"
        margin_str = f"{r['margin_pct']:.0f}%" if r["margin_pct"] is not None else "?"
        flag = ""
        if r["cost"] is None:
            flag = "  <- no cost set, can't verify margin"
        elif r["margin_pct"] is not None and r["margin_pct"] < 50:
            flag = "  <- thin margin for paid ads"
        print(f"{r['title'][:45]:45} {r['price']:>10.2f} {cost_str:>10} {margin_str:>8}{flag}")


_PREMIUM_ELECTRONIC_KEYWORDS = ["steamer", "dryer", "epilator", "shaver"]
_ELECTRONIC_KEYWORDS = ["electric", "ionic", "vibrating", "sonic", "razor", "curler"]


def estimate_cost(title: str, price: float) -> float:
    """Category-based estimate, not a fixed ratio of price — a plastic/
    silicone tool and a battery-powered appliance have genuinely different
    manufacturing cost, so this looks at what the product actually is
    (via title keywords) rather than just deriving cost from price (which
    would be circular and hide real over/under-pricing).

    These are estimates for products with no real supplier cost on record —
    replace with actual AliExpress cost data when available for accuracy.
    """
    t = title.lower()
    if any(k in t for k in _PREMIUM_ELECTRONIC_KEYWORDS):
        base = 12.0
    elif any(k in t for k in _ELECTRONIC_KEYWORDS):
        base = 8.0
    else:
        base = 3.5
    # Small scaling so premium items within a category aren't all identical.
    return round(min(15.0, max(2.0, base + price * 0.03)), 2)


def apply_cost(inventory_item_id: int, cost: float) -> None:
    if not config.SHOPIFY_LIVE:
        log.info("[TEST MODE] Would set inventory item %s cost -> %.2f", inventory_item_id, cost)
        return
    _put(f"inventory_items/{inventory_item_id}.json", {"inventory_item": {"id": inventory_item_id, "cost": str(cost)}})


def estimate_and_apply_missing_costs() -> list[dict]:
    """For every active product with no cost set, estimate one by category
    and push it to Shopify so margin becomes visible going forward. Returns
    the rows that were updated.
    """
    updated = []
    for row in fetch_pricing_overview():
        if row["cost"] is not None:
            continue
        cost = estimate_cost(row["title"], row["price"])
        apply_cost(row["inventory_item_id"], cost)
        updated.append({"title": row["title"], "price": row["price"], "estimated_cost": cost})
    return updated


def suggest_new_price(cost: float, target_margin_pct: float = 70.0) -> float:
    """cost / (1 - target_margin) gives the price that yields target_margin_pct
    gross margin, then rounds to a .95 ending (standard retail convention).
    """
    raw = cost / (1 - target_margin_pct / 100)
    return float(f"{int(raw) }.95") if raw >= 1 else round(raw, 2)


def apply_pricing(product_id: int, variant_id: int, new_price: float) -> None:
    if not config.SHOPIFY_LIVE:
        log.info("[TEST MODE] Would set product %s variant %s price -> %.2f", product_id, variant_id, new_price)
        return
    _put(
        f"products/{product_id}/variants/{variant_id}.json",
        {"variant": {"id": variant_id, "price": str(new_price)}},
    )

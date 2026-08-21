"""Winning-product research.

Real mode calls the configured supplier's trending/best-sellers endpoint
(e.g. CJdropshipping `/product/list` sorted by order volume). Since suppliers
differ in API shape, `fetch_supplier_catalog` is the one function to swap in
for your actual supplier — everything downstream just needs a list of
candidate dicts with the fields used below.

Test mode returns a fixed mock catalog so the rest of the pipeline can be
exercised without any credentials.
"""
import random

from dropship_bot import config
from dropship_bot.models import Product

_MOCK_CATALOG = [
    {
        "supplier_id": "CJ-10234",
        "title": "LED Sunset Projection Lamp",
        "description": "Portable sunset-effect projector lamp for photos, rooms and content creation.",
        "supplier_cost_usd": 6.50,
        "sale_price_usd": 24.99,
        "trend_score": 82,
        "competition_score": 55,
    },
    {
        "supplier_id": "CJ-88213",
        "title": "Posture Corrector Back Brace",
        "description": "Adjustable posture support brace for desk workers.",
        "supplier_cost_usd": 4.20,
        "sale_price_usd": 19.99,
        "trend_score": 61,
        "competition_score": 70,
    },
    {
        "supplier_id": "CJ-55021",
        "title": "Mini Portable Blender Bottle",
        "description": "USB-rechargeable personal blender for smoothies on the go.",
        "supplier_cost_usd": 9.10,
        "sale_price_usd": 29.99,
        "trend_score": 75,
        "competition_score": 40,
    },
    {
        "supplier_id": "CJ-77102",
        "title": "Magnetic Cable Organizer Clips",
        "description": "Set of 6 magnetic clips to route and hide desk cables.",
        "supplier_cost_usd": 2.10,
        "sale_price_usd": 12.99,
        "trend_score": 48,
        "competition_score": 65,
    },
    {
        "supplier_id": "CJ-30456",
        "title": "Pet Hair Removal Roller",
        "description": "Reusable silicone pet hair roller for furniture and clothing.",
        "supplier_cost_usd": 3.30,
        "sale_price_usd": 16.99,
        "trend_score": 69,
        "competition_score": 35,
    },
]


def fetch_supplier_catalog() -> list[dict]:
    if config.TEST_MODE:
        # Small jitter so repeated runs aren't perfectly identical, useful
        # for demoing the ranking logic.
        jittered = []
        for item in _MOCK_CATALOG:
            copy = dict(item)
            copy["trend_score"] = max(0, min(100, copy["trend_score"] + random.randint(-4, 4)))
            jittered.append(copy)
        return jittered

    raise NotImplementedError(
        "Wire this up to your real supplier's trending/best-sellers API "
        "(e.g. CJdropshipping) using config.SUPPLIER_API_KEY, then remove "
        "this guard."
    )


def find_winning_products(top_n: int = 3, min_margin_usd: float = 8.0) -> list[Product]:
    """Rank candidates by opportunity_score (trend momentum minus saturation)
    and return the top N that clear a minimum margin.
    """
    candidates = [Product(**item) for item in fetch_supplier_catalog()]
    viable = [p for p in candidates if p.margin_usd >= min_margin_usd]
    ranked = sorted(viable, key=lambda p: p.opportunity_score, reverse=True)
    return ranked[:top_n]

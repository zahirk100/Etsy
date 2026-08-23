"""Guesses a product's category from its title/description -- shared by
facebook_client (interest targeting) and creative (buyer-persona hints for
ad copy), so the audience we target and the voice we write for stay
consistent instead of drifting independently.
"""
from dropship_bot.models import Product

# Checked in dict order, first match wins -- keep more specific categories
# above more general catch-alls. A product matching none of these just
# gets no category hint (broad targeting, generic copy voice).
CATEGORY_KEYWORDS = {
    "skin care": ["mask", "cleanser", "serum", "skin", "cream", "moisturizer", "peel", "exfoliat"],
    "hair care": ["hair", "curler", "curling", "lash", "eyelash", "shampoo"],
    "jewelry": ["necklace", "bracelet", "earring", "ring", "pendant", "anklet"],
    "sunglasses": ["sunglasses", "eyewear", "shades"],
    "handbags": ["handbag", "purse", "tote", "crossbody", "clutch"],
    "watches": ["watch", "wristwatch"],
    "women's clothing": ["dress", "blouse", "skirt", "jeans", "hoodie", "t-shirt", "shirt", "jacket", "sweater"],
    "fashion accessories": ["belt", "scarf", "hat", "beanie"],
    "beauty": ["roller", "massager", "beauty device", "facial tool", "spa"],
    "novelty gadgets": ["light", "lamp", "gadget", "night light"],
}

# One-line buyer-persona hint per category, fed into the ad-copy prompt so
# copy is written for a specific person rather than a generic "everyone".
# Deliberately not tied to demographic targeting (Meta's Advantage+
# Audience already handles who actually sees it) -- this is purely a
# writing aid to sharpen the voice/hook.
CATEGORY_PERSONAS = {
    "skin care": "a woman 25-45 with an established skincare routine, follows beauty content, "
    "price-conscious but treats herself to things that feel like self-care",
    "hair care": "someone who spends real time on hair/beauty routines and is quick to try a tool "
    "that promises to save time or get a salon-quality result at home",
    "jewelry": "someone shopping for an affordable everyday accessory or a low-risk gift, drawn to "
    "pieces that look more expensive than they are",
    "sunglasses": "someone building their look for the season, cares about trend/style more than "
    "premium-brand names",
    "handbags": "someone who wants a stylish, versatile bag without designer prices",
    "watches": "someone who wants a sharp accessory/gift that reads as higher-end than its price",
    "women's clothing": "someone refreshing their wardrobe for a season/occasion, price-sensitive, "
    "influenced by what they see trending",
    "fashion accessories": "someone accessorizing an outfit on a budget, drawn to versatile pieces",
    "beauty": "someone who wants an at-home version of a spa/salon treatment, values self-care "
    "moments and visible results",
    "novelty gadgets": "someone who saw something like this go viral and wants the small "
    "life-upgrade/novelty it promises, low-commitment impulse buy territory",
}


def guess_category(product: Product) -> str | None:
    text = f"{product.title} {product.description}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return None


def persona_for(category: str | None) -> str | None:
    return CATEGORY_PERSONAS.get(category) if category else None

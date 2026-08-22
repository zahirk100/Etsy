"""Generate ad copy per product.

Template-based by default so this works with zero extra API keys. If you
want LLM-generated copy instead, swap `_generate_text` to call your model of
choice and keep the same return shape — nothing downstream cares how the
text was produced.
"""
from dropship_bot.models import AdCreative, Product

_HEADLINE_TEMPLATES = [
    "{title} — Selling Fast",
    "The {title} Everyone's Talking About",
    "{title}: Today's Deal",
]

_PRIMARY_TEMPLATES = [
    "Meet the {title}. {description} Grab yours before it's back to full price.",
    "{description} Join thousands of happy customers with the {title}.",
]

_DESCRIPTION_TEMPLATES = [
    "Free shipping. Limited stock.",
    "Rated by real customers. Ships worldwide.",
]


def generate_creative(product: Product, variant: int = 0) -> AdCreative:
    headline = _HEADLINE_TEMPLATES[variant % len(_HEADLINE_TEMPLATES)].format(
        title=product.title
    )
    primary_text = _PRIMARY_TEMPLATES[variant % len(_PRIMARY_TEMPLATES)].format(
        title=product.title, description=product.description
    )
    description = _DESCRIPTION_TEMPLATES[variant % len(_DESCRIPTION_TEMPLATES)]
    return AdCreative(
        product=product,
        primary_text=primary_text,
        headline=headline,
        description=description,
    )


def generate_creatives(products: list[Product]) -> list[AdCreative]:
    return [generate_creative(p) for p in products]


def generate_creative_variants(product: Product, n: int = 3) -> list[AdCreative]:
    """N different headline/text combinations for the SAME product, meant to
    be launched as separate ads within one adset. Facebook's delivery system
    then shifts spend toward whichever variant actually performs — proven
    better than betting everything on one fixed headline/copy combination.
    """
    return [generate_creative(product, variant=i) for i in range(n)]

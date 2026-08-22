"""Generate ad copy per product.

Template-based by default so this works with zero extra API keys. If you
want LLM-generated copy instead, swap `_generate_text` to call your model of
choice and keep the same return shape — nothing downstream cares how the
text was produced.
"""
import re

from dropship_bot.models import AdCreative, Product

_MAX_DESCRIPTION_LEN = 140

_HEADLINE_TEMPLATES = [
    "{title} — Free Shipping",
    "Discover the {title}",
    "{title}: Free Shipping Today",
]

_PRIMARY_TEMPLATES = [
    "Meet the {title}. {description} Free shipping on every order, no minimum.",
    "{description} Free shipping included — try the {title} today.",
]

_DESCRIPTION_TEMPLATES = [
    "Free shipping. Easy returns.",
    "Free shipping included.",
]


def _short_description(text: str, max_len: int = _MAX_DESCRIPTION_LEN) -> str:
    """Ad copy needs one punchy line, not a full product page (ingredient
    lists, usage instructions, warnings...). Strip any HTML, then cut at the
    first sentence boundary, falling back to a hard length limit.
    """
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    sentence_end = re.search(r"[.!?]", text)
    if sentence_end and sentence_end.start() < max_len:
        return text[: sentence_end.start() + 1]
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def generate_creative(product: Product, variant: int = 0) -> AdCreative:
    short_description = _short_description(product.description)
    headline = _HEADLINE_TEMPLATES[variant % len(_HEADLINE_TEMPLATES)].format(
        title=product.title
    )
    primary_text = _PRIMARY_TEMPLATES[variant % len(_PRIMARY_TEMPLATES)].format(
        title=product.title, description=short_description
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

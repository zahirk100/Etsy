"""Generate ad copy per product.

Uses Claude to write catchy, benefit-led ad copy when config.ANTHROPIC_API_KEY
is set; falls back to static templates otherwise (or if a generation call
fails) so this always works with zero extra API keys.
"""
import json
import logging
import re

from dropship_bot import config
from dropship_bot.ads import learnings
from dropship_bot.models import AdCreative, Product

log = logging.getLogger(__name__)

_MAX_DESCRIPTION_LEN = 140

_MARKETING_AGENT_SYSTEM_PROMPT = """You are the in-house direct-response marketing copywriter \
for a multi-category e-commerce brand (beauty, fashion accessories, gadgets, and similar trending \
consumer products). You write high-converting Facebook ad copy for cold traffic -- people who have \
never heard of this brand or product before and are scrolling past it in their feed.

Copywriting principles you always follow:
- Lead with the single strongest, most specific benefit or the core desire/pain point the \
product solves -- not generic praise ("amazing", "high-quality") and not a feature dump.
- Short, punchy sentences. Sound like a real person talking to a friend, never a corporate \
brochure or a product-page description.
- No walls of text, no ingredient lists, no usage instructions, no medical or health claims.
- Never invent urgency or scarcity ("selling fast", "limited stock") unless you are explicitly \
told it's true.
- Weave in any guaranteed fact you're given (e.g. free shipping) naturally, never bolted on.
- Every word has to earn its place toward one goal: getting a stranger to stop scrolling, click, \
and buy. If a line doesn't serve that goal, cut it."""

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

# Parallel to the template lists above -- labels the hook each one leans on,
# so template-generated creatives also participate in the learnings loop
# (ads.learnings), not just AI-generated ones.
_ANGLE_LABELS = ["benefit-led", "curiosity"]


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
    angle = _ANGLE_LABELS[variant % len(_ANGLE_LABELS)]
    return AdCreative(
        product=product,
        primary_text=primary_text,
        headline=headline,
        description=description,
        angle=angle,
    )


def generate_creatives(products: list[Product]) -> list[AdCreative]:
    return [generate_creative(p) for p in products]


def _generate_with_ai(product: Product, n: int) -> list[AdCreative] | None:
    """Returns None (never raises) on any failure -- missing key, network
    error, bad JSON, wrong variant count -- so the caller can silently fall
    back to templates.
    """
    if not config.ANTHROPIC_API_KEY:
        return None

    import anthropic

    learnings_block = ""
    past_wins = learnings.summarize_for_prompt()
    if past_wins:
        learnings_block = f"""

What's actually worked in past campaigns for other products in this store \
(use as inspiration for the kind of hook that resonates with this audience -- \
never copy the wording, this is a different product):
{past_wins}"""

    prompt = f"""Write {n} distinct, conversion-focused ad variants for this product.

Product: {product.title}
About it: {_short_description(product.description, 300)}
Price: ${product.sale_price_usd:.2f}
Always true: free shipping on every order.{learnings_block}

For each of the {n} variants, return:
- "headline": max 40 characters, attention-grabbing
- "primary_text": 1-2 short sentences, max ~150 characters
- "description": one short line, max 30 characters (e.g. a mini call-to-action)
- "angle": a 2-4 word label for the hook this variant leans on (e.g. "pain-point", \
"specific-benefit", "social-proof", "curiosity-hook", "before-after") -- this is used to track \
which kinds of hooks work over time, so be precise and consistent rather than inventing a new \
label for the same idea.

Make the {n} variants genuinely different angles (e.g. different hook, different benefit \
emphasized, different tone) so they can be tested against each other -- not minor rewordings.

Respond with ONLY a JSON array of exactly {n} objects with those four keys. No markdown, \
no code fences, no explanation -- just the raw JSON array."""

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            system=_MARKETING_AGENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next(b.text for b in response.content if b.type == "text").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        variants = json.loads(text)
        creatives = [
            AdCreative(
                product=product,
                headline=v["headline"],
                primary_text=v["primary_text"],
                description=v["description"],
                angle=v.get("angle", ""),
            )
            for v in variants[:n]
        ]
        if len(creatives) != n:
            raise ValueError(f"expected {n} variants, got {len(creatives)}")
        return creatives
    except Exception:
        log.warning("AI ad copy generation failed, falling back to templates", exc_info=True)
        return None


def generate_creative_variants(product: Product, n: int = 3) -> list[AdCreative]:
    """N different headline/text combinations for the SAME product, meant to
    be launched as separate ads within one adset. Facebook's delivery system
    then shifts spend toward whichever variant actually performs — proven
    better than betting everything on one fixed headline/copy combination.
    """
    return _generate_with_ai(product, n) or [generate_creative(product, variant=i) for i in range(n)]

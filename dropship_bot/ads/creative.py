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
from dropship_bot.ads.categorize import guess_category, persona_for
from dropship_bot.models import AdCreative, Product

log = logging.getLogger(__name__)

_MAX_DESCRIPTION_LEN = 140

_MARKETING_AGENT_SYSTEM_PROMPT = """You are the in-house direct-response marketing copywriter \
for a multi-category e-commerce brand (beauty, fashion accessories, gadgets, and similar trending \
consumer products). You write high-converting Facebook ad copy for cold traffic -- people who have \
never heard of this brand or product before and are scrolling past it in their feed with zero \
intent to buy anything. Your job is to interrupt that scroll and turn a stranger into an impulse \
buyer in under 3 seconds of reading.

Before writing, work out silently: what specific frustration, insecurity, or small daily annoyance \
does this product make disappear? Not the feature ("16 RGB colors") but the felt pain underneath it \
("fumbling for a light switch in the dark", "spending 20 minutes every morning wrestling curly \
hair"). Every variant should be a different way IN to that same underlying pain or desire.

Copywriting principles you always follow:
- Lead with the single strongest, most specific benefit or pain point -- not generic praise \
("amazing", "high-quality") and not a feature dump.
- Short, punchy sentences. Sound like a real person talking to a friend, never a corporate \
brochure or a product-page description.
- No walls of text, no ingredient lists, no usage instructions, no medical or health claims.
- Never invent urgency, scarcity, review counts, or testimonials ("selling fast", "10,000 happy \
customers") unless you are explicitly told they're true -- fabricated social proof is a lie, full \
stop, not just a style choice to avoid.
- Weave in any guaranteed fact you're given (e.g. free shipping) naturally, never bolted on.
- Make the payoff feel immediate and low-risk -- impulse purchases happen when the buyer pictures \
themselves enjoying the result right now, not when they're asked to deliberate. Concrete, specific, \
sensory language beats vague adjectives every time ("stops the ache in your hands after five \
minutes" beats "so comfortable").
- Every word has to earn its place toward one goal: getting a stranger to stop scrolling, click, \
and buy. If a line doesn't serve that goal, cut it."""

# Fixed taxonomy of strategic angles, cycled across variants so the 3 ads
# genuinely test different psychological entry points into the same pain
# point instead of just different wording of the same idea. PAS
# (Problem-Agitate-Solve) is a standard direct-response copywriting
# framework: name the problem, twist the knife on how annoying/costly it
# is, then land the product as the immediate fix.
_STRATEGIC_ANGLES = [
    (
        "problem-agitate-solve",
        "Open by naming the specific daily annoyance/pain point, spend one line making it feel "
        "more frustrating/relatable than the reader had consciously noticed, then land the product "
        "as the immediate, obvious fix.",
    ),
    (
        "curiosity-pattern-interrupt",
        "Open with something unexpected enough to stop a scroll -- a surprising fact, an odd "
        "visual, or a claim that makes the reader need to know how -- then resolve it with the "
        "product.",
    ),
    (
        "instant-gratification",
        "Skip the setup. Open straight on the specific, vivid payoff the buyer gets and how fast/"
        "easily they get it -- make the reward feel close enough to reach out and take right now.",
    ),
]

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
# using the same taxonomy as _STRATEGIC_ANGLES so template-generated
# creatives feed the learnings loop (ads.learnings) with comparable labels
# to AI-generated ones, not a separate incompatible vocabulary.
_ANGLE_LABELS = ["problem-agitate-solve", "instant-gratification"]


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

    persona = persona_for(guess_category(product))
    persona_block = f"\nWho you're writing for: {persona}." if persona else ""

    angle_lines = "\n".join(
        f'{i + 1}. "{label}" -- {instruction}'
        for i, (label, instruction) in enumerate(
            _STRATEGIC_ANGLES[j % len(_STRATEGIC_ANGLES)] for j in range(n)
        )
    )

    prompt = f"""Write {n} distinct, conversion-focused ad variants for this product.

Product: {product.title}
About it: {_short_description(product.description, 300)}
Price: ${product.sale_price_usd:.2f}
Always true: free shipping on every order.{persona_block}{learnings_block}

Write each variant using the strategic angle assigned to it below, in order (cycling back to the \
first angle if there are more variants than angles):
{angle_lines}

For each of the {n} variants, return:
- "headline": max 40 characters, attention-grabbing
- "primary_text": 1-2 short sentences, max ~150 characters
- "description": one short line, max 30 characters (e.g. a mini call-to-action)
- "angle": the exact angle label assigned above for that variant

Respond with ONLY a JSON array of exactly {n} objects with those four keys, in the same order as \
the angles above. No markdown, no code fences, no explanation -- just the raw JSON array."""

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

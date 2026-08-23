"""End-to-end orchestration: research -> push to Shopify -> generate ad
creative -> launch Facebook campaign (paused) -> activate at the conservative
starting budget. Monitoring/scaling happens separately via monitoring.loop,
run on your own schedule (see cli.py `monitor`).

Campaigns always launch PAUSED and are only flipped ACTIVE here, at
config.STARTING_DAILY_BUDGET_USD — never at an inflated budget — so a bad
run can only ever risk one day's worth of the starting budget before the
monitoring loop's guardrails get a chance to react.
"""
import logging
from datetime import datetime, timezone

from dropship_bot import config
from dropship_bot.ads import creative as creative_module
from dropship_bot.ads import facebook_client
from dropship_bot.models import Campaign
from dropship_bot.research import trending
from dropship_bot.store import best_sellers, shopify_client

log = logging.getLogger(__name__)


class PartialLaunchFailure(Exception):
    """Raised by _launch_and_activate when creation or activation fails
    partway through. Carries every Campaign already created on Facebook so
    the caller can still save them to state before propagating the failure --
    without this, a transient error (e.g. Facebook's own 500s) would turn
    real, already-created Facebook campaigns into untracked orphans: invisible
    to guardrails and monitoring, but still real and potentially spending.
    """

    def __init__(self, campaigns: list[Campaign], original: Exception):
        super().__init__(str(original))
        self.campaigns = campaigns
        self.original = original


def _launch_and_activate(picks: list[tuple]) -> list[Campaign]:
    """Shared by every entry point that turns (product, listing) pairs into
    live campaigns: generate creative variants, launch paused, then activate
    at the conservative starting budget.

    On success, returns every created Campaign. On failure partway through,
    raises PartialLaunchFailure carrying whatever campaigns were already
    created -- callers must catch it, save those to state, and then decide
    whether to re-raise so the failure stays visible (e.g. a failed CI job).
    """
    campaigns: list[Campaign] = []
    try:
        for product, listing in picks:
            creatives = creative_module.generate_creative_variants(product)
            campaign = facebook_client.launch_campaign(
                creatives,
                listing,
                daily_budget_usd=config.STARTING_DAILY_BUDGET_USD,
                country=config.TARGET_COUNTRY,
            )
            campaigns.append(campaign)

        for campaign in campaigns:
            facebook_client.set_campaign_status(campaign, "ACTIVE")
            campaign.last_budget_change_at = datetime.now(timezone.utc)
    except Exception as exc:
        log.error(
            "Launch cycle failed partway through -- %d campaign(s) already created on Facebook. "
            "Raising PartialLaunchFailure so the caller can still save them to state.",
            len(campaigns),
        )
        raise PartialLaunchFailure(campaigns, exc) from exc

    return campaigns


def run_launch_cycle(top_n: int = 3) -> list[Campaign]:
    log.info("=== 1/4 Researching winning products (AliExpress live=%s) ===", config.ALIEXPRESS_LIVE)
    products = trending.find_winning_products(top_n=top_n)
    for p in products:
        log.info(
            "  candidate: %-35s margin=$%.2f trend=%.0f comp=%.0f opportunity=%.1f",
            p.title,
            p.margin_usd,
            p.trend_score,
            p.competition_score,
            p.opportunity_score,
        )

    log.info("=== 2/4 Pushing to Shopify ===")
    listings = shopify_client.push_products(products)

    log.info("=== 3/4 Generating ad creative + launching campaigns (paused) ===")
    log.info(
        "=== 4/4 Activating campaigns at conservative starting budget ($%.2f/day, cap $%.2f/day) ===",
        config.STARTING_DAILY_BUDGET_USD,
        config.DAILY_BUDGET_CAP_USD,
    )
    return _launch_and_activate(list(zip(products, listings)))


def run_launch_cycle_for_existing_products(
    top_n: int = 3, exclude_supplier_ids: set[str] | None = None
) -> list[Campaign]:
    """Same as run_launch_cycle, but skips research + Shopify push entirely
    and instead advertises products already live in the store — for a store
    that already has a catalog, this is the faster path to a first live
    campaign.

    exclude_supplier_ids: pass every product that already has a campaign
    (active OR paused) in state, same idea top_up_campaigns already uses --
    otherwise this has no memory of previous launches and can just re-pick
    the same top-ranked products again instead of genuinely new ones.
    """
    log.info("=== 1/3 Selecting best existing products to advertise ===")
    picks = best_sellers.pick_products_to_advertise(
        top_n=top_n, exclude_supplier_ids=exclude_supplier_ids
    )
    for product, listing in picks:
        log.info("  %-40s %s", product.title, listing.product_url)

    log.info("=== 2/3 Generating ad creative + launching campaigns (paused) ===")
    log.info(
        "=== 3/3 Activating campaigns at conservative starting budget ($%.2f/day, cap $%.2f/day) ===",
        config.STARTING_DAILY_BUDGET_USD,
        config.DAILY_BUDGET_CAP_USD,
    )
    return _launch_and_activate(picks)


def top_up_campaigns(
    existing_campaigns: list[Campaign], target_active: int | None = None
) -> list[Campaign]:
    """Call after monitoring.loop.run_once(): if pausing losers dropped the
    active count below config.TARGET_ACTIVE_CAMPAIGNS, launch that many new,
    previously-untested products to fill the open slots — this is what
    keeps the test pool replenished instead of just shrinking over time.
    Returns only the newly launched campaigns (append to your campaign list).
    """
    target_active = config.TARGET_ACTIVE_CAMPAIGNS if target_active is None else target_active
    active_count = sum(1 for c in existing_campaigns if c.status == "ACTIVE")
    needed = target_active - active_count
    if needed <= 0:
        return []

    if not config.SHOPIFY_LIVE:
        log.info(
            "=== Topping up: %d open slot(s), but Shopify isn't live -- skipping (nothing real to pick from) ===",
            needed,
        )
        return []

    log.info("=== Topping up: %d open slot(s), finding untested product(s) ===", needed)
    already_tried = {c.product.supplier_id for c in existing_campaigns}
    picks = best_sellers.pick_products_to_advertise(top_n=needed, exclude_supplier_ids=already_tried)
    if not picks:
        log.info("  No untested products left in the store to add.")
        return []
    for product, listing in picks:
        log.info("  + %-40s %s", product.title, listing.product_url)

    return _launch_and_activate(picks)

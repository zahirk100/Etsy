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
    campaigns = []
    for product, listing in zip(products, listings):
        creatives = creative_module.generate_creative_variants(product)
        campaign = facebook_client.launch_campaign(
            creatives,
            listing,
            daily_budget_usd=config.STARTING_DAILY_BUDGET_USD,
            country=config.TARGET_COUNTRY,
        )
        campaigns.append(campaign)

    log.info(
        "=== 4/4 Activating campaigns at conservative starting budget ($%.2f/day, cap $%.2f/day) ===",
        config.STARTING_DAILY_BUDGET_USD,
        config.DAILY_BUDGET_CAP_USD,
    )
    for campaign in campaigns:
        facebook_client.set_campaign_status(campaign, "ACTIVE")
        campaign.last_budget_change_at = datetime.now(timezone.utc)

    return campaigns


def run_launch_cycle_for_existing_products(top_n: int = 3) -> list[Campaign]:
    """Same as run_launch_cycle, but skips research + Shopify push entirely
    and instead advertises products already live in the store — for a store
    that already has a catalog, this is the faster path to a first live
    campaign.
    """
    log.info("=== 1/3 Selecting best existing products to advertise ===")
    picks = best_sellers.pick_products_to_advertise(top_n=top_n)
    for product, listing in picks:
        log.info("  %-40s %s", product.title, listing.product_url)

    log.info("=== 2/3 Generating ad creative + launching campaigns (paused) ===")
    campaigns = []
    for product, listing in picks:
        creatives = creative_module.generate_creative_variants(product)
        campaign = facebook_client.launch_campaign(
            creatives,
            listing,
            daily_budget_usd=config.STARTING_DAILY_BUDGET_USD,
            country=config.TARGET_COUNTRY,
        )
        campaigns.append(campaign)

    log.info(
        "=== 3/3 Activating campaigns at conservative starting budget ($%.2f/day, cap $%.2f/day) ===",
        config.STARTING_DAILY_BUDGET_USD,
        config.DAILY_BUDGET_CAP_USD,
    )
    for campaign in campaigns:
        facebook_client.set_campaign_status(campaign, "ACTIVE")
        campaign.last_budget_change_at = datetime.now(timezone.utc)

    return campaigns

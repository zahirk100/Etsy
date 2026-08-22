"""Create and adjust campaigns via the Meta Marketing API.

Docs: https://developers.facebook.com/docs/marketing-apis
Requires a System User access token with ads_management scope
(config.FACEBOOK_ACCESS_TOKEN) and an ad account id (act_...).
"""
import logging

import requests

from dropship_bot import config
from dropship_bot.models import AdCreative, Campaign, CampaignInsights, ShopifyListing

log = logging.getLogger(__name__)

_GRAPH_BASE = f"https://graph.facebook.com/{config.FACEBOOK_API_VERSION}"


def _post(path: str, payload: dict) -> dict:
    payload = {**payload, "access_token": config.FACEBOOK_ACCESS_TOKEN}
    resp = requests.post(f"{_GRAPH_BASE}/{path}", data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict) -> dict:
    params = {**params, "access_token": config.FACEBOOK_ACCESS_TOKEN}
    resp = requests.get(f"{_GRAPH_BASE}/{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def launch_campaign(
    creative: AdCreative,
    listing: ShopifyListing,
    daily_budget_usd: float = config.STARTING_DAILY_BUDGET_USD,
    country: str = config.TARGET_COUNTRY,
) -> Campaign:
    """Create a paused-by-default campaign + adset + ad. Caller decides when
    to flip it ACTIVE (pipeline does this only after guardrail checks pass).
    """
    if not config.FACEBOOK_LIVE:
        fake = abs(hash(listing.shopify_product_id))
        log.info(
            "[TEST MODE] Would launch FB campaign for '%s' | budget $%.2f/day | country=%s | headline='%s'",
            creative.product.title,
            daily_budget_usd,
            country,
            creative.headline,
        )
        return Campaign(
            product=creative.product,
            campaign_id=f"test-camp-{fake % 100000}",
            adset_id=f"test-adset-{fake % 100000}",
            ad_id=f"test-ad-{fake % 100000}",
            daily_budget_usd=daily_budget_usd,
            country=country,
            status="PAUSED",
        )

    account = config.FACEBOOK_AD_ACCOUNT_ID

    campaign = _post(
        f"{account}/campaigns",
        {
            "name": f"Auto - {creative.product.title}",
            "objective": "OUTCOME_SALES",
            "status": "PAUSED",
            "special_ad_categories": "[]",
        },
    )

    adset = _post(
        f"{account}/adsets",
        {
            "name": f"Auto adset - {creative.product.title}",
            "campaign_id": campaign["id"],
            "daily_budget": int(daily_budget_usd * 100),  # cents
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "targeting": {
                "geo_locations": {"countries": [country]},
                "age_min": 18,
            },
            "status": "PAUSED",
        },
    )

    creative_obj = _post(
        f"{account}/adcreatives",
        {
            "name": f"Auto creative - {creative.product.title}",
            "object_story_spec": {
                "page_id": config.FACEBOOK_PAGE_ID,
                "link_data": {
                    "link": listing.product_url,
                    "message": creative.primary_text,
                    "name": creative.headline,
                    "description": creative.description,
                    "image_url": creative.product.image_urls[0]
                    if creative.product.image_urls
                    else None,
                },
            },
        },
    )

    ad = _post(
        f"{account}/ads",
        {
            "name": f"Auto ad - {creative.product.title}",
            "adset_id": adset["id"],
            "creative": {"creative_id": creative_obj["id"]},
            "status": "PAUSED",
        },
    )

    return Campaign(
        product=creative.product,
        campaign_id=campaign["id"],
        adset_id=adset["id"],
        ad_id=ad["id"],
        daily_budget_usd=daily_budget_usd,
        country=country,
        status="PAUSED",
    )


def set_campaign_status(campaign: Campaign, status: str) -> None:
    """status: 'ACTIVE' or 'PAUSED'."""
    if not config.FACEBOOK_LIVE:
        log.info("[TEST MODE] Would set campaign %s status -> %s", campaign.campaign_id, status)
        campaign.status = status
        return
    _post(campaign.campaign_id, {"status": status})
    campaign.status = status


def set_daily_budget(campaign: Campaign, new_daily_budget_usd: float) -> None:
    if not config.FACEBOOK_LIVE:
        log.info(
            "[TEST MODE] Would change adset %s budget $%.2f -> $%.2f",
            campaign.adset_id,
            campaign.daily_budget_usd,
            new_daily_budget_usd,
        )
        campaign.daily_budget_usd = new_daily_budget_usd
        return
    _post(campaign.adset_id, {"daily_budget": int(new_daily_budget_usd * 100)})
    campaign.daily_budget_usd = new_daily_budget_usd


def get_insights(campaign: Campaign) -> CampaignInsights:
    if not config.FACEBOOK_LIVE:
        import random

        spend = round(random.uniform(5, 40), 2)
        purchases = random.choices([0, 1, 2, 3, 4], weights=[30, 25, 20, 15, 10])[0]
        revenue = round(purchases * campaign.product.sale_price_usd, 2)
        return CampaignInsights(
            campaign_id=campaign.campaign_id,
            spend_usd=spend,
            purchases=purchases,
            revenue_usd=revenue,
        )

    data = _get(
        f"{campaign.campaign_id}/insights",
        {"fields": "spend,actions,action_values", "date_preset": "today"},
    )
    rows = data.get("data", [])
    if not rows:
        return CampaignInsights(campaign_id=campaign.campaign_id, spend_usd=0, purchases=0, revenue_usd=0)
    row = rows[0]
    spend = float(row.get("spend", 0))
    purchases = 0
    revenue = 0.0
    for action in row.get("actions", []):
        if action.get("action_type") == "purchase":
            purchases = int(action.get("value", 0))
    for action in row.get("action_values", []):
        if action.get("action_type") == "purchase":
            revenue = float(action.get("value", 0))
    return CampaignInsights(
        campaign_id=campaign.campaign_id, spend_usd=spend, purchases=purchases, revenue_usd=revenue
    )

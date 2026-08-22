"""Create and adjust campaigns via the Meta Marketing API.

Docs: https://developers.facebook.com/docs/marketing-apis
Requires a System User access token with ads_management scope
(config.FACEBOOK_ACCESS_TOKEN) and an ad account id (act_...).
"""
import json
import logging

import requests

from dropship_bot import config
from dropship_bot.models import AdCreative, Campaign, CampaignInsights, ShopifyListing

log = logging.getLogger(__name__)

_GRAPH_BASE = f"https://graph.facebook.com/{config.FACEBOOK_API_VERSION}"


def _raise_with_body(resp: requests.Response) -> None:
    """requests' default raise_for_status() drops the response body, which
    is exactly where Facebook puts the actually-useful error message/code —
    surface it instead of a bare '400 Client Error'.
    """
    if not resp.ok:
        raise requests.exceptions.HTTPError(
            f"{resp.status_code} error from Facebook for {resp.url}: {resp.text}", response=resp
        )


def _post(path: str, payload: dict) -> dict:
    # Facebook's API takes form-encoded requests, which has no concept of a
    # nested object -- dict/list values (targeting, object_story_spec,
    # creative, ...) must be sent as JSON text, not requests' default
    # str(dict) form-encoding (which Facebook can't parse at all).
    payload = {
        k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in payload.items()
    }
    payload["access_token"] = config.FACEBOOK_ACCESS_TOKEN
    resp = requests.post(f"{_GRAPH_BASE}/{path}", data=payload, timeout=30)
    _raise_with_body(resp)
    return resp.json()


def _get(path: str, params: dict) -> dict:
    params = {**params, "access_token": config.FACEBOOK_ACCESS_TOKEN}
    resp = requests.get(f"{_GRAPH_BASE}/{path}", params=params, timeout=30)
    _raise_with_body(resp)
    return resp.json()


def launch_campaign(
    creatives: list[AdCreative],
    listing: ShopifyListing,
    daily_budget_usd: float = config.STARTING_DAILY_BUDGET_USD,
    country: str = config.TARGET_COUNTRY,
) -> Campaign:
    """Create a paused-by-default campaign + adset, with one ad per creative
    variant in `creatives` (all sharing the adset's single budget) so
    Facebook's delivery system can shift spend toward whichever
    headline/copy variant actually performs, instead of betting everything
    on one fixed ad. Caller decides when to flip it ACTIVE (pipeline does
    this only after guardrail checks pass).
    """
    product = creatives[0].product

    if config.FACEBOOK_LIVE and not config.FACEBOOK_PIXEL_ID:
        raise RuntimeError(
            "FACEBOOK_PIXEL_ID is not set. The adset's 'promoted object' needs it to tell "
            "Facebook which conversion events (purchases) to optimize for and report on -- "
            "find it in Events Manager (business.facebook.com/events_manager) under the "
            "Pixel connected via Shopify's Facebook & Instagram sales channel."
        )

    if not config.FACEBOOK_LIVE:
        fake = abs(hash(listing.shopify_product_id))
        log.info(
            "[TEST MODE] Would launch FB campaign for '%s' | budget $%.2f/day | country=%s | %d creative variant(s): %s",
            product.title,
            daily_budget_usd,
            country,
            len(creatives),
            [c.headline for c in creatives],
        )
        return Campaign(
            product=product,
            campaign_id=f"test-camp-{fake % 100000}",
            adset_id=f"test-adset-{fake % 100000}",
            ad_ids=[f"test-ad-{fake % 100000}-{i}" for i in range(len(creatives))],
            daily_budget_usd=daily_budget_usd,
            country=country,
            status="PAUSED",
        )

    account = config.FACEBOOK_AD_ACCOUNT_ID

    campaign = _post(
        f"{account}/campaigns",
        {
            "name": f"Auto - {product.title}",
            "objective": "OUTCOME_SALES",
            "status": "PAUSED",
            "special_ad_categories": [],
            # Required by Meta whenever there's no campaign-level budget --
            # we intentionally budget at the adset level instead so our own
            # guardrail rules control scaling, not Facebook's auto-optimizer.
            "is_adset_budget_sharing_enabled": "false",
        },
    )

    adset = _post(
        f"{account}/adsets",
        {
            "name": f"Auto adset - {product.title}",
            "campaign_id": campaign["id"],
            "daily_budget": int(daily_budget_usd * 100),  # cents
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "targeting": {
                "geo_locations": {"countries": [country]},
                "age_min": 18,
            },
            "promoted_object": {
                "pixel_id": config.FACEBOOK_PIXEL_ID,
                "custom_event_type": "PURCHASE",
            },
            "status": "PAUSED",
        },
    )

    ad_ids = []
    for i, creative in enumerate(creatives):
        creative_obj = _post(
            f"{account}/adcreatives",
            {
                "name": f"Auto creative {i} - {product.title}",
                "object_story_spec": {
                    "page_id": config.FACEBOOK_PAGE_ID,
                    "link_data": {
                        "link": listing.product_url,
                        "message": creative.primary_text,
                        "name": creative.headline,
                        "description": creative.description,
                        "picture": creative.product.image_urls[0]
                        if creative.product.image_urls
                        else None,
                    },
                },
            },
        )
        ad = _post(
            f"{account}/ads",
            {
                "name": f"Auto ad {i} - {product.title}",
                "adset_id": adset["id"],
                "creative": {"creative_id": creative_obj["id"]},
                "status": "PAUSED",
            },
        )
        ad_ids.append(ad["id"])

    return Campaign(
        product=product,
        campaign_id=campaign["id"],
        adset_id=adset["id"],
        ad_ids=ad_ids,
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

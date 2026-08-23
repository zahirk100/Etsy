"""Create and adjust campaigns via the Meta Marketing API.

Docs: https://developers.facebook.com/docs/marketing-apis
Requires a System User access token with ads_management scope
(config.FACEBOOK_ACCESS_TOKEN) and an ad account id (act_...).
"""
import json
import logging
import urllib.parse

import requests

from dropship_bot import config
from dropship_bot.ads.categorize import guess_category
from dropship_bot.models import AdCreative, Campaign, CampaignInsights, Product, ShopifyListing

log = logging.getLogger(__name__)

_GRAPH_BASE = f"https://graph.facebook.com/{config.FACEBOOK_API_VERSION}"


def _split_countries(country: str) -> list[str]:
    """DROPSHIP_TARGET_COUNTRY can be a single code ("GB") or a
    comma-separated list ("GB,IE,AU,NZ") -- Meta's geo_locations.countries
    already accepts multiple codes in one adset, so multi-country targeting
    needs no extra campaigns/budget-splitting, just a wider countries list
    on the same adset.
    """
    return [c.strip() for c in country.split(",") if c.strip()]


def _with_utm(url: str, product_title: str) -> str:
    """Tags the destination link with UTM params so paid traffic is
    distinguishable from organic/other channels in Shopify/GA analytics --
    separate from (and in addition to) what the Pixel/Conversions API
    already reports for the ad platform's own attribution.
    """
    sep = "&" if "?" in url else "?"
    params = urllib.parse.urlencode(
        {"utm_source": "facebook", "utm_medium": "paid_social", "utm_campaign": product_title}
    )
    return f"{url}{sep}{params}"

def _resolve_interests(query: str, limit: int = 3) -> list[dict]:
    """Look up real Meta interest-targeting IDs for a category keyword via
    the Ads Targeting Search endpoint (GET /search?type=adinterest).

    Best-effort and NEVER allowed to block a launch: any failure (network,
    permissions, zero matches) just means the campaign falls back to plain
    broad targeting, same as before this existed. Unverified against a real
    ad account -- check the interests it actually picks (via the log line
    in launch_campaign) before trusting it, a generic query can surface
    loosely-related interests that narrow the audience for no benefit. Also
    worth weighing against the alternative: Meta's own delivery algorithm
    under OFFSITE_CONVERSIONS optimization often finds buyers better than
    manual interest-narrowing, especially at small daily budgets -- if
    performance looks worse than pre-targeting baseline, prefer setting
    DROPSHIP_INTEREST_TARGETING_ENABLED=false over tuning the keyword lists.
    """
    try:
        data = _get("search", {"type": "adinterest", "q": query, "limit": limit})
        return [{"id": i["id"], "name": i["name"]} for i in data.get("data", [])[:limit]]
    except Exception:
        log.warning("Interest lookup for %r failed, falling back to broad targeting", query, exc_info=True)
        return []


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
            ad_creatives=creatives,
        )

    account = config.FACEBOOK_AD_ACCOUNT_ID

    targeting = {
        "geo_locations": {"countries": _split_countries(country)},
        "age_min": 18,
        # Advantage+ Audience: lets Meta's delivery engine expand beyond our
        # interest list when it finds better opportunities, instead of
        # being hard-capped to exactly what we specified. Current Meta best
        # practice for OUTCOME_SALES campaigns -- narrow manual targeting
        # increasingly underperforms letting the algorithm widen the pool.
        "targeting_automation": {"advantage_audience": 1},
    }
    if config.INTEREST_TARGETING_ENABLED:
        interest_query = guess_category(product)
        interests = _resolve_interests(interest_query) if interest_query else []
        if interests:
            targeting["flexible_spec"] = [{"interests": interests}]
            log.info(
                "Targeting '%s' with interests: %s", product.title, [i["name"] for i in interests]
            )
        elif interest_query:
            log.info(
                "No interest matches for '%s' (query %r) — falling back to broad targeting",
                product.title,
                interest_query,
            )

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

    def _build_adset_payload(t: dict) -> dict:
        return {
            "name": f"Auto adset - {product.title}",
            "campaign_id": campaign["id"],
            "daily_budget": int(daily_budget_usd * 100),  # cents
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "targeting": t,
            "promoted_object": {
                "pixel_id": config.FACEBOOK_PIXEL_ID,
                "custom_event_type": "PURCHASE",
            },
            "status": "PAUSED",
        }

    try:
        adset = _post(f"{account}/adsets", _build_adset_payload(targeting))
    except requests.exceptions.HTTPError as e:
        # The Ads Targeting Search endpoint (_resolve_interests) can hand
        # back interest IDs Meta itself has since deprecated -- it doesn't
        # reject them at search time, only at adset-creation time (error
        # code 100 / subcode 1870247, "some detailed targeting options were
        # combined/deprecated"). Rather than fail the whole launch over an
        # optional targeting refinement, drop it and retry broad -- this is
        # the fail-open behavior _resolve_interests was already documented
        # to have, just also needed here where it can actually surface.
        if "flexible_spec" in targeting and "error_subcode\":1870247" in str(e):
            log.warning(
                "Interest targeting for '%s' included deprecated option(s), retrying broad: %s",
                product.title,
                e,
            )
            targeting = {k: v for k, v in targeting.items() if k != "flexible_spec"}
            adset = _post(f"{account}/adsets", _build_adset_payload(targeting))
        else:
            raise

    images = product.image_urls
    ad_ids = []
    for i, creative in enumerate(creatives):
        # Cycle through the product's real photos across variants (instead
        # of always the first) so Facebook can also learn which image
        # performs best, not just which headline/copy.
        picture = images[i % len(images)] if images else None
        creative_obj = _post(
            f"{account}/adcreatives",
            {
                "name": f"Auto creative {i} - {product.title}",
                "object_story_spec": {
                    "page_id": config.FACEBOOK_PAGE_ID,
                    "link_data": {
                        "link": _with_utm(listing.product_url, product.title),
                        "message": creative.primary_text,
                        "name": creative.headline,
                        "description": creative.description,
                        "picture": picture,
                        "call_to_action": {"type": "SHOP_NOW"},
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
        ad_creatives=creatives,
    )


def get_campaign_status(campaign_id: str) -> str:
    """Read-only: the campaign's actual current status on Facebook, so
    local state (.dropship_state.json) can be reconciled after a manual
    change made directly in Ads Manager (outside this bot's own
    set_campaign_status/pause-all/etc.) -- otherwise state silently drifts
    from reality and the active-campaign count used for auto-replenish
    becomes wrong.
    """
    if not config.FACEBOOK_LIVE:
        return "PAUSED"
    return _get(campaign_id, {"fields": "status"})["status"]


def diagnose_delivery(campaign: Campaign) -> dict:
    """Read-only: pulls status + effective_status for the campaign, its
    adset, and every ad, plus any ad-level review issues -- effective_status
    is what actually determines delivery (e.g. a campaign/adset can show
    plain "ACTIVE" while effective_status is "PENDING_REVIEW",
    "DISAPPROVED", "CAMPAIGN_PAUSED" via a parent, "ADSET_PAUSED", or an
    account-level "WITH_ISSUES"/"IN_PROCESS" -- which is invisible if you
    only check the top-level status field, and is the usual reason a
    campaign shows real spend of $0 long after being "activated").
    """
    if not config.FACEBOOK_LIVE:
        return {"campaign": {"status": "PAUSED", "effective_status": "PAUSED"}, "adset": {}, "ads": []}

    campaign_data = _get(campaign.campaign_id, {"fields": "status,effective_status"})
    adset_data = _get(campaign.adset_id, {"fields": "status,effective_status"})
    ads = []
    for ad_id in campaign.ad_ids:
        ad_data = _get(ad_id, {"fields": "status,effective_status,issues_info"})
        ads.append(ad_data)
    return {"campaign": campaign_data, "adset": adset_data, "ads": ads}


def set_campaign_status(campaign: Campaign, status: str) -> None:
    """status: 'ACTIVE' or 'PAUSED'. Cascades to the adset and every ad --
    Facebook only actually delivers when campaign, adset, AND ad are all
    ACTIVE, so flipping just the campaign leaves it looking active while the
    adset/ads underneath stay dark.
    """
    if not config.FACEBOOK_LIVE:
        log.info(
            "[TEST MODE] Would set campaign %s (+ adset + %d ad(s)) status -> %s",
            campaign.campaign_id,
            len(campaign.ad_ids),
            status,
        )
        campaign.status = status
        return
    _post(campaign.campaign_id, {"status": status})
    _post(campaign.adset_id, {"status": status})
    for ad_id in campaign.ad_ids:
        _post(ad_id, {"status": status})
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


# Meta reports purchases under different action_types depending on the
# pixel/CAPI setup and API version -- "omni_purchase" is the deduplicated
# cross-channel total when present, "purchase" is the common pixel-era
# alias, and "offsite_conversion.fb_pixel_purchase" is the legacy pixel-only
# type. Checked in priority order (not summed) to avoid double-counting the
# same conversion reported under more than one type.
_PURCHASE_ACTION_TYPES = ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"]


def _first_matching_action(actions: list[dict], action_types: list[str]) -> float:
    by_type = {a.get("action_type"): a.get("value", 0) for a in actions}
    for action_type in action_types:
        if action_type in by_type:
            return float(by_type[action_type])
    return 0.0


def get_insights(campaign: Campaign) -> CampaignInsights:
    if not config.FACEBOOK_LIVE:
        import random

        spend = round(random.uniform(5, 40), 2)
        purchases = random.choices([0, 1, 2, 3, 4], weights=[30, 25, 20, 15, 10])[0]
        revenue = round(purchases * campaign.product.sale_price_usd, 2)
        # Roughly a 1-3% CTR on the simulated spend, so the fake data also
        # exercises the minimum-clicks-before-kill gate realistically.
        link_clicks = int(spend * random.uniform(2, 6))
        return CampaignInsights(
            campaign_id=campaign.campaign_id,
            spend_usd=spend,
            purchases=purchases,
            revenue_usd=revenue,
            link_clicks=link_clicks,
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
    actions = row.get("actions", [])
    purchases = int(_first_matching_action(actions, _PURCHASE_ACTION_TYPES))
    revenue = _first_matching_action(row.get("action_values", []), _PURCHASE_ACTION_TYPES)
    link_clicks = int(_first_matching_action(actions, ["link_click"]))
    return CampaignInsights(
        campaign_id=campaign.campaign_id,
        spend_usd=spend,
        purchases=purchases,
        revenue_usd=revenue,
        link_clicks=link_clicks,
    )


def get_ad_level_insights(campaign: Campaign) -> dict[str, int]:
    """Link-clicks per ad_id within this campaign's single adset -- used
    only to figure out which of the N creative variants actually won, for
    the creative-learnings feedback loop (ads.learnings). The pause/scale
    decision itself only needs the campaign-level total from get_insights.
    """
    if not config.FACEBOOK_LIVE:
        import random

        return {ad_id: random.randint(0, 40) for ad_id in campaign.ad_ids}

    data = _get(
        f"{campaign.campaign_id}/insights",
        {"fields": "actions", "level": "ad", "date_preset": "today"},
    )
    return {
        row["ad_id"]: int(_first_matching_action(row.get("actions", []), ["link_click"]))
        for row in data.get("data", [])
        if "ad_id" in row
    }

"""Pulls live performance for each active campaign, applies the guardrail
rules, and executes the resulting action. Call `run_once` from a scheduler
(cron, Celery beat, etc.) — this module does not sleep/loop itself so the
caller controls cadence and can log/alert between runs.
"""
import logging
from datetime import datetime, timezone

from dropship_bot.ads import facebook_client
from dropship_bot.models import Campaign
from dropship_bot.monitoring.rules import Action, decide

log = logging.getLogger(__name__)


def run_once(campaigns: list[Campaign]) -> list[dict]:
    results = []
    for campaign in campaigns:
        if campaign.status == "PAUSED" and campaign.last_budget_change_at is not None:
            # Already paused by a previous rule breach — leave it off.
            results.append({"campaign_id": campaign.campaign_id, "action": "already_paused"})
            continue

        insights = facebook_client.get_insights(campaign)
        decision = decide(campaign, insights)
        log.info(
            "Campaign %s (%s): spend=$%.2f purchases=%d -> %s | %s",
            campaign.campaign_id,
            campaign.product.title,
            insights.spend_usd,
            insights.purchases,
            decision.action.value,
            decision.reason,
        )

        if decision.action == Action.PAUSE:
            facebook_client.set_campaign_status(campaign, "PAUSED")
            campaign.last_budget_change_at = datetime.now(timezone.utc)
        elif decision.action == Action.SCALE_UP:
            facebook_client.set_daily_budget(campaign, decision.new_daily_budget_usd)
            campaign.last_budget_change_at = datetime.now(timezone.utc)
        elif decision.action == Action.ACTIVATE:
            facebook_client.set_campaign_status(campaign, "ACTIVE")
            campaign.last_budget_change_at = datetime.now(timezone.utc)
        # NONE and HOLD_COOLDOWN require no API call.

        results.append(
            {
                "campaign_id": campaign.campaign_id,
                "product": campaign.product.title,
                "action": decision.action.value,
                "reason": decision.reason,
                "daily_budget_usd": campaign.daily_budget_usd,
            }
        )
    return results

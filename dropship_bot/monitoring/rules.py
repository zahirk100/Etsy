"""Guardrail decision logic. Pure functions — no API calls here — so the
policy is easy to unit test and reason about independent of Facebook/Shopify
wiring.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from dropship_bot import config
from dropship_bot.models import Campaign, CampaignInsights


class Action(Enum):
    NONE = "none"
    ACTIVATE = "activate"       # first-time go-live after launch, still capped
    SCALE_UP = "scale_up"
    PAUSE = "pause"
    HOLD_COOLDOWN = "hold_cooldown"  # would act, but cooldown blocks it


@dataclass
class Decision:
    action: Action
    new_daily_budget_usd: float
    reason: str


def _in_cooldown(campaign: Campaign) -> bool:
    if campaign.last_budget_change_at is None:
        return False
    elapsed_hours = (
        datetime.now(timezone.utc) - campaign.last_budget_change_at
    ).total_seconds() / 3600
    return elapsed_hours < config.BUDGET_CHANGE_COOLDOWN_HOURS


def decide(campaign: Campaign, insights: CampaignInsights) -> Decision:
    """Single source of truth for what the automation is allowed to do.

    Order of checks matters: a hard CPA breach pauses immediately even
    within cooldown (protecting the budget cap always wins), scaling is the
    only action subject to cooldown.
    """
    if insights.spend_usd < config.MIN_SPEND_BEFORE_JUDGING_USD:
        return Decision(
            Action.NONE,
            campaign.daily_budget_usd,
            f"Only ${insights.spend_usd:.2f} spent so far, below "
            f"${config.MIN_SPEND_BEFORE_JUDGING_USD:.2f} threshold needed to judge performance.",
        )

    cpa = insights.cpa_usd
    if cpa is not None and cpa > config.PAUSE_IF_CPA_ABOVE_USD:
        return Decision(
            Action.PAUSE,
            campaign.daily_budget_usd,
            f"CPA ${cpa:.2f} exceeds pause threshold ${config.PAUSE_IF_CPA_ABOVE_USD:.2f}.",
        )

    if insights.purchases == 0:
        return Decision(
            Action.PAUSE,
            campaign.daily_budget_usd,
            f"Zero purchases after ${insights.spend_usd:.2f} spend.",
        )

    roas = insights.roas
    if roas is not None and roas >= config.SCALE_IF_ROAS_ABOVE:
        if _in_cooldown(campaign):
            return Decision(
                Action.HOLD_COOLDOWN,
                campaign.daily_budget_usd,
                f"ROAS {roas:.2f}x qualifies for scaling but budget was changed "
                f"less than {config.BUDGET_CHANGE_COOLDOWN_HOURS:.0f}h ago.",
            )
        proposed = campaign.daily_budget_usd * (1 + config.MAX_DAILY_BUDGET_INCREASE_PCT / 100)
        capped = min(proposed, config.DAILY_BUDGET_CAP_USD)
        if capped <= campaign.daily_budget_usd:
            return Decision(
                Action.NONE,
                campaign.daily_budget_usd,
                f"ROAS {roas:.2f}x is good but budget already at cap ${config.DAILY_BUDGET_CAP_USD:.2f}.",
            )
        return Decision(
            Action.SCALE_UP,
            round(capped, 2),
            f"ROAS {roas:.2f}x >= {config.SCALE_IF_ROAS_ABOVE}x target, scaling "
            f"${campaign.daily_budget_usd:.2f} -> ${capped:.2f} (capped at ${config.DAILY_BUDGET_CAP_USD:.2f}).",
        )

    return Decision(
        Action.NONE,
        campaign.daily_budget_usd,
        f"ROAS {roas if roas is not None else 0:.2f}x is below scale target "
        f"{config.SCALE_IF_ROAS_ABOVE}x but CPA is acceptable — holding steady.",
    )

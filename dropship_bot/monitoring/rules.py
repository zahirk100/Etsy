"""Guardrail decision logic. Pure functions — no API calls here — so the
policy is easy to unit test and reason about independent of Facebook/Shopify
wiring.

Policy (deliberately simple/aggressive for the low-budget testing phase):
- 1+ purchase -> scale the budget up (subject to cooldown + cap), UNLESS the
  cost per purchase is already above the pain threshold — a sale at a
  terrible CPA is a reason to pause, not scale.
- 0 purchases once spend passes a percentage of that day's budget -> pause.
  Checking spend as a % of budget (not an absolute euro floor) means the
  kill-switch reacts proportionally whether a campaign is running at €5/day
  or €20/day.
- Otherwise: still gathering data, hold steady.
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
    """Single source of truth for what the automation is allowed to do."""
    if insights.purchases >= config.SCALE_IF_PURCHASES_AT_LEAST:
        cpa = insights.cpa_usd
        if cpa is not None and cpa > config.PAUSE_IF_CPA_ABOVE_USD:
            return Decision(
                Action.PAUSE,
                campaign.daily_budget_usd,
                f"{insights.purchases} purchase(s) but CPA ${cpa:.2f} exceeds "
                f"${config.PAUSE_IF_CPA_ABOVE_USD:.2f} — not profitable enough to scale.",
            )
        if _in_cooldown(campaign):
            return Decision(
                Action.HOLD_COOLDOWN,
                campaign.daily_budget_usd,
                f"{insights.purchases} purchase(s), qualifies for scaling but budget was "
                f"changed less than {config.BUDGET_CHANGE_COOLDOWN_HOURS:.0f}h ago.",
            )
        proposed = campaign.daily_budget_usd * (1 + config.MAX_DAILY_BUDGET_INCREASE_PCT / 100)
        capped = min(proposed, config.DAILY_BUDGET_CAP_USD)
        if capped <= campaign.daily_budget_usd:
            return Decision(
                Action.NONE,
                campaign.daily_budget_usd,
                f"{insights.purchases} purchase(s) but budget already at cap ${config.DAILY_BUDGET_CAP_USD:.2f}.",
            )
        return Decision(
            Action.SCALE_UP,
            round(capped, 2),
            f"{insights.purchases} purchase(s) — scaling ${campaign.daily_budget_usd:.2f} -> "
            f"${capped:.2f} (capped at ${config.DAILY_BUDGET_CAP_USD:.2f}).",
        )

    spend_pct = (
        insights.spend_usd / campaign.daily_budget_usd * 100 if campaign.daily_budget_usd else 0
    )
    if spend_pct >= config.PAUSE_IF_SPEND_PCT_OF_BUDGET_WITH_NO_SALE:
        return Decision(
            Action.PAUSE,
            campaign.daily_budget_usd,
            f"${insights.spend_usd:.2f} spent ({spend_pct:.0f}% of ${campaign.daily_budget_usd:.2f} "
            f"daily budget) with zero purchases — past the "
            f"{config.PAUSE_IF_SPEND_PCT_OF_BUDGET_WITH_NO_SALE:.0f}% kill threshold.",
        )

    return Decision(
        Action.NONE,
        campaign.daily_budget_usd,
        f"${insights.spend_usd:.2f} spent ({spend_pct:.0f}% of budget), 0 purchases — "
        f"still under the {config.PAUSE_IF_SPEND_PCT_OF_BUDGET_WITH_NO_SALE:.0f}% kill threshold.",
    )

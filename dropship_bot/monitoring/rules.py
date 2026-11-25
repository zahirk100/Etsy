"""Guardrail decision logic. Pure functions — no API calls here — so the
policy is easy to unit test and reason about independent of Facebook/Shopify
wiring.

Policy -- every number is the campaign's LIFETIME total (since launch), not
just today. The previous version judged on "today" only, which reset every
night and paused almost every product after a single $5 day.

- 1+ purchase:
  - CPA above the pain threshold (and enough spend to trust it) -> pause.
  - otherwise scale the budget up, subject to cooldown, the per-campaign cap
    and the account-wide total cap.
- 0 purchases:
  - spent >= KILL_SPEND with >= MIN_LINK_CLICKS clicks -> pause (fair test,
    no sale).
  - spent >= HARD_STOP regardless of clicks -> pause (broken / no-CTR ad).
  - otherwise hold: still gathering data.
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


def decide(
    campaign: Campaign, insights: CampaignInsights, total_active_budget_usd: float | None = None
) -> Decision:
    """Single source of truth for what the automation is allowed to do.

    total_active_budget_usd: sum of daily budgets of all ACTIVE campaigns
    (including this one). Scaling never pushes it past
    config.MAX_TOTAL_DAILY_BUDGET_USD. None = don't check (tests/manual use).
    """
    budget = campaign.daily_budget_usd
    spend = insights.spend_usd

    if insights.purchases >= config.SCALE_IF_PURCHASES_AT_LEAST and insights.purchases > 0:
        cpa = insights.cpa_usd
        if cpa is not None and cpa > config.PAUSE_IF_CPA_ABOVE_USD and spend >= 1.5 * config.PAUSE_IF_CPA_ABOVE_USD:
            return Decision(
                Action.PAUSE,
                budget,
                f"{insights.purchases} purchase(s) but CPA ${cpa:.2f} exceeds "
                f"${config.PAUSE_IF_CPA_ABOVE_USD:.2f} after ${spend:.2f} spend — not profitable enough.",
            )
        if cpa is not None and cpa > config.PAUSE_IF_CPA_ABOVE_USD:
            return Decision(
                Action.NONE,
                budget,
                f"{insights.purchases} purchase(s), CPA ${cpa:.2f} is high but spend ${spend:.2f} is still "
                "too low to judge — holding, not scaling.",
            )
        if _in_cooldown(campaign):
            return Decision(
                Action.HOLD_COOLDOWN,
                budget,
                f"{insights.purchases} purchase(s), qualifies for scaling but budget was "
                f"changed less than {config.BUDGET_CHANGE_COOLDOWN_HOURS:.0f}h ago.",
            )
        proposed = budget * (1 + config.MAX_DAILY_BUDGET_INCREASE_PCT / 100)
        capped = min(proposed, config.DAILY_BUDGET_CAP_USD)
        if total_active_budget_usd is not None:
            headroom = config.MAX_TOTAL_DAILY_BUDGET_USD - (total_active_budget_usd - budget)
            capped = min(capped, headroom)
        capped = round(capped, 2)
        if capped <= budget:
            return Decision(
                Action.NONE,
                budget,
                f"{insights.purchases} purchase(s) but already at the budget cap "
                f"(per campaign ${config.DAILY_BUDGET_CAP_USD:.2f}, total ${config.MAX_TOTAL_DAILY_BUDGET_USD:.2f}).",
            )
        return Decision(
            Action.SCALE_UP,
            capped,
            f"{insights.purchases} purchase(s), CPA {'$%.2f' % cpa if cpa else 'n/a'} — scaling "
            f"${budget:.2f} -> ${capped:.2f}.",
        )

    if insights.purchases > 0:
        return Decision(
            Action.NONE,
            budget,
            f"{insights.purchases} purchase(s), below the {config.SCALE_IF_PURCHASES_AT_LEAST} needed to "
            "scale — holding.",
        )
    if spend >= config.HARD_STOP_SPEND_NO_SALE_USD:
        return Decision(
            Action.PAUSE,
            budget,
            f"${spend:.2f} spent in total, {insights.link_clicks} link click(s), zero purchases — "
            f"past the ${config.HARD_STOP_SPEND_NO_SALE_USD:.2f} hard stop.",
        )
    if spend >= config.KILL_SPEND_NO_SALE_USD:
        if insights.link_clicks >= config.MIN_LINK_CLICKS_BEFORE_KILL:
            return Decision(
                Action.PAUSE,
                budget,
                f"${spend:.2f} spent in total, {insights.link_clicks} link click(s), zero purchases — "
                f"past the ${config.KILL_SPEND_NO_SALE_USD:.2f} kill threshold.",
            )
        return Decision(
            Action.NONE,
            budget,
            f"${spend:.2f} spent, 0 purchases, but only {insights.link_clicks} link click(s) "
            f"(need {config.MIN_LINK_CLICKS_BEFORE_KILL}) — holding until the "
            f"${config.HARD_STOP_SPEND_NO_SALE_USD:.2f} hard stop.",
        )
    return Decision(
        Action.NONE,
        budget,
        f"${spend:.2f} spent in total, 0 purchases — still under the "
        f"${config.KILL_SPEND_NO_SALE_USD:.2f} kill threshold.",
    )

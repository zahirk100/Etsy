"""Minimal JSON persistence so `launch` and `monitor` can run as separate
CLI invocations (e.g. one cron job launching new products, another running
every hour to check performance). Swap for a real database once this moves
beyond a single-machine prototype.
"""
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from dropship_bot.models import AdCreative, Campaign, Product

STATE_FILE = Path(__file__).parent.parent / ".dropship_state.json"


def save_campaigns(campaigns: list[Campaign]) -> None:
    data = [_campaign_to_dict(c) for c in campaigns]
    STATE_FILE.write_text(json.dumps(data, indent=2))


def load_campaigns() -> list[Campaign]:
    if not STATE_FILE.exists():
        return []
    data = json.loads(STATE_FILE.read_text())
    return [_campaign_from_dict(d) for d in data]


def _campaign_to_dict(c: Campaign) -> dict:
    d = asdict(c)
    if c.last_budget_change_at is not None:
        d["last_budget_change_at"] = c.last_budget_change_at.isoformat()
    return d


def _campaign_from_dict(d: dict) -> Campaign:
    product = Product(**d["product"])
    last_change = (
        datetime.fromisoformat(d["last_budget_change_at"])
        if d.get("last_budget_change_at")
        else None
    )
    # Campaigns saved before ad_creatives existed just won't feed the
    # creative-learnings loop -- nothing to backfill it from.
    ad_creatives = [
        AdCreative(product=product, **{k: v for k, v in c.items() if k != "product"})
        for c in d.get("ad_creatives", [])
    ]
    return Campaign(
        product=product,
        campaign_id=d["campaign_id"],
        adset_id=d["adset_id"],
        ad_ids=d["ad_ids"],
        daily_budget_usd=d["daily_budget_usd"],
        country=d["country"],
        status=d["status"],
        last_budget_change_at=last_change,
        ad_creatives=ad_creatives,
    )

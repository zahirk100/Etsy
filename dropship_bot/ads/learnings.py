"""Tracks which ad-copy angles have actually won or lost, so future
generations lean on what's worked instead of guessing blind every time.

Pure JSON file, same "swap for a real DB before this runs at real scale"
tradeoff as state.py. Deliberately dumb: no ML, just a rolling log of
outcomes that gets summarized into a few lines of plain text for the
ad-copy prompt (see ads.creative).
"""
import json
from pathlib import Path

LEARNINGS_FILE = Path(__file__).parent.parent.parent / ".dropship_learnings.json"

# Keep the file small and the prompt short -- only recent outcomes matter,
# since audience/creative fatigue and trends shift over time anyway.
_MAX_ENTRIES = 50


def _load() -> list[dict]:
    if not LEARNINGS_FILE.exists():
        return []
    return json.loads(LEARNINGS_FILE.read_text())


def record_outcome(product_title: str, angle: str, headline: str, outcome: str) -> None:
    """outcome: 'won' (campaign scaled up) or 'lost' (campaign paused)."""
    if not angle and not headline:
        return
    entries = _load()
    entries.append(
        {"product": product_title, "angle": angle, "headline": headline, "outcome": outcome}
    )
    LEARNINGS_FILE.write_text(json.dumps(entries[-_MAX_ENTRIES:], indent=2))


def summarize_for_prompt(max_examples: int = 5) -> str:
    """Short digest of recently-winning angles, meant to be dropped into
    the ad-copy generation prompt. Empty string if there's no data yet
    (e.g. brand new setup, or nothing has scaled yet) -- callers should
    skip the section entirely rather than print an empty one.
    """
    wins = [e for e in _load() if e["outcome"] == "won"]
    if not wins:
        return ""
    lines = [
        f'- "{w["angle"]}" angle worked for {w["product"]} (e.g. headline "{w["headline"]}")'
        for w in wins[-max_examples:]
    ]
    return "\n".join(lines)

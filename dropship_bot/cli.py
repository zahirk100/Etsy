"""Entry point.

    python -m dropship_bot.cli launch [--top-n 3]
    python -m dropship_bot.cli monitor

`launch` runs the research -> Shopify -> Facebook launch cycle once and
saves the resulting campaigns to .dropship_state.json. `monitor` loads that
state, checks live performance against the guardrails, scales/pauses as
needed, and saves the updated state back. Run `monitor` on a schedule (cron,
systemd timer, etc.) — it does one pass per invocation by design, so you
control the cadence rather than trusting a long-running loop.
"""
import argparse
import logging

from dropship_bot import config, pipeline, state
from dropship_bot.monitoring import loop as monitoring_loop


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Automated dropshipping pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    launch_parser = sub.add_parser("launch", help="Research products, push to Shopify, launch ads")
    launch_parser.add_argument("--top-n", type=int, default=3)

    sub.add_parser("monitor", help="Check performance and apply guardrail rules")

    args = parser.parse_args()

    mode = "TEST MODE (no real API calls, no ad spend)" if config.TEST_MODE else "LIVE MODE — real money will be spent"
    logging.info("Running in %s\n", mode)

    if args.command == "launch":
        campaigns = pipeline.run_launch_cycle(top_n=args.top_n)
        existing = state.load_campaigns()
        state.save_campaigns(existing + campaigns)
        logging.info("\nLaunched %d campaign(s). State saved to %s", len(campaigns), state.STATE_FILE)

    elif args.command == "monitor":
        campaigns = state.load_campaigns()
        if not campaigns:
            logging.info("No campaigns in state (%s). Run `launch` first.", state.STATE_FILE)
            return
        results = monitoring_loop.run_once(campaigns)
        state.save_campaigns(campaigns)
        logging.info("\nChecked %d campaign(s):", len(results))
        for r in results:
            logging.info("  %s", r)


if __name__ == "__main__":
    main()

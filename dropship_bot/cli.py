"""Entry point.

    python -m dropship_bot.cli launch [--top-n 3]
    python -m dropship_bot.cli monitor
    python -m dropship_bot.cli set-budget --amount 10

`launch` runs the research -> Shopify -> Facebook launch cycle once and
saves the resulting campaigns to .dropship_state.json. `monitor` loads that
state, checks live performance against the guardrails, scales/pauses as
needed, and saves the updated state back. Run `monitor` on a schedule (cron,
systemd timer, etc.) — it does one pass per invocation by design, so you
control the cadence rather than trusting a long-running loop. `set-budget`
is a manual one-off override for every currently-ACTIVE campaign (e.g. "test
faster this week"), outside the normal guardrail scaling `monitor` does.
"""
import argparse
import logging

from dropship_bot import config, pipeline, state
from dropship_bot.ads import facebook_client
from dropship_bot.monitoring import loop as monitoring_loop
from dropship_bot.store import inventory, pricing, shipping


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Automated dropshipping pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    launch_parser = sub.add_parser("launch", help="Research new products, push to Shopify, launch ads")
    launch_parser.add_argument("--top-n", type=int, default=3)

    existing_parser = sub.add_parser(
        "launch-existing", help="Launch ads for the store's best existing products (no new products)"
    )
    existing_parser.add_argument("--top-n", type=int, default=3)

    sub.add_parser("monitor", help="Check performance and apply guardrail rules")

    sub.add_parser(
        "setup-store",
        help="One-time store fixes: free shipping on any zone missing a rate, estimate missing product costs",
    )

    set_budget_parser = sub.add_parser(
        "set-budget",
        help="Manually set the daily budget on every currently-ACTIVE campaign (a deliberate "
        "one-off override, outside the normal cooldown/max-increase guardrails -- use for a "
        "conscious test decision, not routine scaling, which `monitor` already handles).",
    )
    set_budget_parser.add_argument("--amount", type=float, required=True)

    args = parser.parse_args()

    def _status(live: bool) -> str:
        return "LIVE" if live else "test-mode"

    logging.info(
        "Shopify: %s | Facebook: %s | AliExpress: %s\n",
        _status(config.SHOPIFY_LIVE),
        _status(config.FACEBOOK_LIVE),
        _status(config.ALIEXPRESS_LIVE),
    )

    if args.command == "launch":
        campaigns = pipeline.run_launch_cycle(top_n=args.top_n)
        existing = state.load_campaigns()
        state.save_campaigns(existing + campaigns)
        logging.info("\nLaunched %d campaign(s). State saved to %s", len(campaigns), state.STATE_FILE)

    elif args.command == "launch-existing":
        campaigns = pipeline.run_launch_cycle_for_existing_products(top_n=args.top_n)
        existing = state.load_campaigns()
        state.save_campaigns(existing + campaigns)
        logging.info("\nLaunched %d campaign(s). State saved to %s", len(campaigns), state.STATE_FILE)

    elif args.command == "setup-store":
        logging.info("=== Inventory (disabling tracking so sold-out can't reoccur) ===")
        changed = inventory.ensure_inventory_not_tracked_everywhere()
        if not changed:
            logging.info("  No variants had inventory tracking on.")
        for row in changed:
            logging.info("  %s: inventory tracking disabled", row["product_title"])

        logging.info("\n=== Shipping ===")
        for action in shipping.ensure_free_shipping_everywhere():
            logging.info("  %s", action)

        logging.info("\n=== Estimating missing product costs ===")
        updated = pricing.estimate_and_apply_missing_costs()
        if not updated:
            logging.info("  All active products already have a cost set.")
        for row in updated:
            logging.info(
                "  %-45s price=%.2f estimated_cost=%.2f (%.0f%% margin)",
                row["title"][:45],
                row["price"],
                row["estimated_cost"],
                (row["price"] - row["estimated_cost"]) / row["price"] * 100,
            )

        logging.info("\n=== Updated margin overview ===")
        rows = pricing.fetch_pricing_overview()
        pricing.print_pricing_overview(rows)

    elif args.command == "monitor":
        campaigns = state.load_campaigns()
        if not campaigns:
            logging.info("No campaigns in state (%s). Run `launch` first.", state.STATE_FILE)
            return
        results = monitoring_loop.run_once(campaigns)
        logging.info("\nChecked %d campaign(s):", len(results))
        for r in results:
            logging.info("  %s", r)

        new_campaigns = pipeline.top_up_campaigns(campaigns)
        campaigns.extend(new_campaigns)
        if new_campaigns:
            logging.info("\nLaunched %d new campaign(s) to fill open slot(s).", len(new_campaigns))

        state.save_campaigns(campaigns)

    elif args.command == "set-budget":
        from datetime import datetime, timezone

        amount = min(args.amount, config.DAILY_BUDGET_CAP_USD)
        if amount < args.amount:
            logging.warning(
                "Requested $%.2f exceeds DROPSHIP_DAILY_BUDGET_CAP_USD=$%.2f -- capping at $%.2f.",
                args.amount,
                config.DAILY_BUDGET_CAP_USD,
                amount,
            )

        campaigns = state.load_campaigns()
        active = [c for c in campaigns if c.status == "ACTIVE"]
        if not active:
            logging.info("No ACTIVE campaigns in state (%s) -- nothing to do.", state.STATE_FILE)
            return

        for campaign in active:
            logging.info(
                "%s (%s): $%.2f -> $%.2f", campaign.campaign_id, campaign.product.title,
                campaign.daily_budget_usd, amount,
            )
            facebook_client.set_daily_budget(campaign, amount)
            campaign.last_budget_change_at = datetime.now(timezone.utc)

        state.save_campaigns(campaigns)
        logging.info(
            "\nSet daily budget to $%.2f on %d active campaign(s). Note: this starts a fresh "
            "%dh cooldown on each, same as any other budget change -- `monitor` won't auto-scale "
            "them again until it passes.",
            amount, len(active), int(config.BUDGET_CHANGE_COOLDOWN_HOURS),
        )


if __name__ == "__main__":
    main()

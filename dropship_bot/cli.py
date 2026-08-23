"""Entry point.

    python -m dropship_bot.cli launch [--top-n 3]
    python -m dropship_bot.cli monitor
    python -m dropship_bot.cli set-budget --amount 10
    python -m dropship_bot.cli test-aliexpress [--keywords "..."]

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

    update_countries_parser = sub.add_parser(
        "update-targeting-countries",
        help="Replace the geo targeting on every campaign in state whose current country list "
        "contains one of --drop (default: AU) with --countries (default: config.TARGET_COUNTRY). "
        "Use this when a country turns out to require ad-account verification you don't want to "
        "complete (e.g. AU) -- updates already-created adsets directly, doesn't just change future "
        "launches.",
    )
    update_countries_parser.add_argument(
        "--drop", default="AU", help="Comma-separated country code(s) to look for and remove"
    )
    update_countries_parser.add_argument(
        "--countries", default=None, help="New comma-separated country list (default: config.TARGET_COUNTRY)"
    )

    apply_update_parser = sub.add_parser(
        "apply-product-update",
        help="Push a title/description rewrite to an existing live product, loaded from "
        "dropship_bot/store/pending_product_updates/<id>.json (keys: title, body_html, either "
        "optional). Use inspect-product first to review the current listing.",
    )
    apply_update_parser.add_argument("--id", required=True, help="Shopify product ID")

    sub.add_parser(
        "diagnose-delivery",
        help="Read-only: for every ACTIVE campaign, print campaign/adset/ad "
        "status + effective_status + any review issues -- use this when a campaign shows 0 spend "
        "for an extended period to see whether it's stuck in ad review, disapproved, or has a "
        "billing/account-level block.",
    )

    sub.add_parser(
        "sync-state",
        help="Read-only against Facebook, writes local state only: reconciles "
        "campaign.status in .dropship_state.json against each campaign's actual status on "
        "Facebook, for when a campaign was paused/activated manually in Ads Manager instead of "
        "through this bot. Doesn't change anything on Facebook itself.",
    )

    inspect_product_parser = sub.add_parser(
        "inspect-product",
        help="Read-only: fetch one product's full detail (title, description, images, price) by "
        "title substring match, to review/improve its listing. No side effects.",
    )
    inspect_product_parser.add_argument("--query", required=True, help="Substring to match in the product title")

    test_ae_parser = sub.add_parser(
        "test-aliexpress",
        help="Dry-run the AliExpress Affiliate API connection: fetch candidates and print them, "
        "with NO Shopify/Facebook side effects and nothing written to state. Use this to verify "
        "credentials and response parsing before ever running `launch` for real.",
    )
    test_ae_parser.add_argument("--keywords", default=None, help="Overrides ALIEXPRESS_SEARCH_KEYWORDS for this run")
    test_ae_parser.add_argument("--page-size", type=int, default=10)

    sub.add_parser(
        "find-orphans",
        help="Read-only: list every campaign in the ad account that is NOT present in local "
        "state (.dropship_state.json), with its real campaign/adset/ad ids and statuses -- use "
        "this after any launch that crashed before state was saved, to find campaigns that are "
        "real on Facebook (and possibly spending) but invisible to guardrails/monitoring.",
    )

    sub.add_parser(
        "pause-all",
        help="Pause every currently-ACTIVE campaign (e.g. to stop spend while retooling product "
        "sourcing/strategy). Doesn't touch state otherwise -- re-activate manually in Ads Manager, "
        "or launch fresh campaigns, when ready to resume.",
    )

    sub.add_parser(
        "adopt-orphans",
        help="Find every 'Auto - <product>' campaign in the ad account that's missing from "
        "local state, match it back to the real Shopify product by title, and save it into "
        "state (activating it if it isn't already) so it's no longer invisible to guardrails/"
        "monitoring. Use this after a launch crashed before state was saved (see find-orphans).",
    )

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
        existing = state.load_campaigns()
        try:
            campaigns = pipeline.run_launch_cycle(top_n=args.top_n)
        except pipeline.PartialLaunchFailure as exc:
            state.save_campaigns(existing + exc.campaigns)
            logging.error(
                "\nLaunch failed partway through -- %d campaign(s) already created on Facebook "
                "were saved to state (%s) so they stay tracked. Original error: %s",
                len(exc.campaigns),
                state.STATE_FILE,
                exc.original,
            )
            raise
        state.save_campaigns(existing + campaigns)
        logging.info("\nLaunched %d campaign(s). State saved to %s", len(campaigns), state.STATE_FILE)

    elif args.command == "launch-existing":
        existing = state.load_campaigns()
        already_tried = {c.product.supplier_id for c in existing}
        try:
            campaigns = pipeline.run_launch_cycle_for_existing_products(
                top_n=args.top_n, exclude_supplier_ids=already_tried
            )
        except pipeline.PartialLaunchFailure as exc:
            state.save_campaigns(existing + exc.campaigns)
            logging.error(
                "\nLaunch failed partway through -- %d campaign(s) already created on Facebook "
                "were saved to state (%s) so they stay tracked. Original error: %s",
                len(exc.campaigns),
                state.STATE_FILE,
                exc.original,
            )
            raise
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

        try:
            new_campaigns = pipeline.top_up_campaigns(campaigns)
        except pipeline.PartialLaunchFailure as exc:
            campaigns.extend(exc.campaigns)
            state.save_campaigns(campaigns)
            logging.error(
                "\nTop-up failed partway through -- %d campaign(s) already created on Facebook "
                "were saved to state (%s) so they stay tracked. Original error: %s",
                len(exc.campaigns),
                state.STATE_FILE,
                exc.original,
            )
            raise
        campaigns.extend(new_campaigns)
        if new_campaigns:
            logging.info("\nLaunched %d new campaign(s) to fill open slot(s).", len(new_campaigns))

        state.save_campaigns(campaigns)

    elif args.command == "diagnose-delivery":
        account = facebook_client.get_account_status()
        status_name = facebook_client.ACCOUNT_STATUS_NAMES.get(
            account.get("account_status"), account.get("account_status")
        )
        logging.info("Ad account status: %s (code %s)", status_name, account.get("account_status"))
        if account.get("disable_reason"):
            logging.info("Disable reason code: %s", account["disable_reason"])
        funding = account.get("funding_source_details")
        logging.info("Funding source: %s", funding if funding else "none confirmed")
        business = account.get("business")
        if business:
            logging.info(
                "Business: %s (id=%s) verification_status=%s",
                business.get("name"),
                business.get("id"),
                business.get("verification_status"),
            )
        else:
            logging.info("Business: none linked (or not visible with current token permissions)")

        campaigns = [c for c in state.load_campaigns() if c.status == "ACTIVE"]
        if not campaigns:
            logging.info("\nNo ACTIVE campaigns in state (%s).", state.STATE_FILE)
            return

        for campaign in campaigns:
            diag = facebook_client.diagnose_delivery(campaign)
            logging.info("\n%s (%s)", campaign.product.title, campaign.campaign_id)
            logging.info(
                "  Campaign: status=%s effective_status=%s",
                diag["campaign"].get("status"),
                diag["campaign"].get("effective_status"),
            )
            logging.info(
                "  Adset:    status=%s effective_status=%s",
                diag["adset"].get("status"),
                diag["adset"].get("effective_status"),
            )
            for i, ad in enumerate(diag["ads"]):
                logging.info(
                    "  Ad %d:     status=%s effective_status=%s",
                    i,
                    ad.get("status"),
                    ad.get("effective_status"),
                )
                if ad.get("issues_info"):
                    logging.info("            issues: %s", ad["issues_info"])

    elif args.command == "apply-product-update":
        import json
        from pathlib import Path

        from dropship_bot.store import shopify_client

        update_file = (
            Path(__file__).parent / "store" / "pending_product_updates" / f"{args.id}.json"
        )
        if not update_file.exists():
            logging.info("No pending update file at %s", update_file)
            return
        data = json.loads(update_file.read_text())
        logging.info("Applying update to product %s: %s", args.id, list(data.keys()))
        shopify_client.update_product_copy(
            args.id, title=data.get("title"), body_html=data.get("body_html")
        )
        logging.info("Done.")

    elif args.command == "sync-state":
        campaigns = state.load_campaigns()
        if not campaigns:
            logging.info("No campaigns in state (%s).", state.STATE_FILE)
            return

        changed = 0
        for campaign in campaigns:
            real_status = facebook_client.get_campaign_status(campaign.campaign_id)
            if real_status != campaign.status:
                logging.info(
                    "%s (%s): state said %s, Facebook says %s -- updating state",
                    campaign.campaign_id,
                    campaign.product.title,
                    campaign.status,
                    real_status,
                )
                campaign.status = real_status
                changed += 1

        state.save_campaigns(campaigns)
        logging.info(
            "\nChecked %d campaign(s), reconciled %d status mismatch(es).", len(campaigns), changed
        )

    elif args.command == "inspect-product":
        from dropship_bot.store import inspect as store_inspect

        product = store_inspect.find_product(args.query)
        if product is None:
            logging.info("No product title matching %r found. All products in the store (any status):", args.query)
            for p in store_inspect.list_all_products():
                logging.info("  [%s] %s", p["status"], p["title"])
            return

        variant = product["variants"][0] if product.get("variants") else {}
        logging.info("Title:       %s", product["title"])
        logging.info("Status:      %s", product["status"])
        logging.info("Price:       %s", variant.get("price", "?"))
        logging.info("Product ID:  %s", product["id"])
        logging.info("Handle:      %s", product["handle"])
        logging.info("Variants:    %d (%s)", len(product.get("variants", [])), ", ".join(
            v.get("title", "?") for v in product.get("variants", [])
        ))
        logging.info("Images (%d):", len(product.get("images", [])))
        for img in product.get("images", []):
            logging.info("  %s", img.get("src"))
        logging.info("\nDescription (raw body_html):\n%s", product.get("body_html", "(empty)"))

    elif args.command == "test-aliexpress":
        from dropship_bot.research import aliexpress as aliexpress_research

        if not (config.ALIEXPRESS_APP_KEY and config.ALIEXPRESS_APP_SECRET):
            logging.info(
                "ALIEXPRESS_APP_KEY/ALIEXPRESS_APP_SECRET not set -- nothing to test. "
                "(This command only exercises the live Affiliate API, not the CSV path.)"
            )
            return
        if not config.ALIEXPRESS_TRACKING_ID:
            logging.warning(
                "ALIEXPRESS_TRACKING_ID is not set -- the API may reject the request "
                "(it's a required param for aliexpress.affiliate.hotproduct.query)."
            )

        keywords = args.keywords if args.keywords is not None else config.ALIEXPRESS_SEARCH_KEYWORDS
        logging.info("Querying AliExpress Affiliate API (keywords=%r, page_size=%d)...\n", keywords, args.page_size)
        try:
            products = aliexpress_research.fetch_via_affiliate_api(keywords=keywords, page_size=args.page_size)
        except aliexpress_research.AliExpressAPIError as e:
            logging.error("%s", e)
            return

        if not products:
            logging.info(
                "0 products returned. If this is unexpected, the response shape likely doesn't "
                "match _parse_hotproducts()'s assumptions -- check the AliExpressAPIError message "
                "above (if any), or add a temporary print(raw) in _call() to inspect the raw body."
            )
            return

        logging.info("%d product(s):\n", len(products))
        for p in products:
            logging.info(
                "  %-45s cost=$%.2f -> price=$%.2f  trend=%.0f  id=%s\n    image=%s",
                p["title"][:45],
                p["supplier_cost_usd"],
                p["sale_price_usd"],
                p["trend_score"],
                p["supplier_id"],
                (p["image_urls"][0] if p["image_urls"] else "(none)"),
            )

    elif args.command == "find-orphans":
        known_ids = {c.campaign_id for c in state.load_campaigns()}
        account_campaigns = facebook_client.list_account_campaigns()
        orphans = [c for c in account_campaigns if c["id"] not in known_ids]

        if not orphans:
            logging.info(
                "No orphans found -- every campaign in the ad account (%d checked) is present "
                "in local state (%s).",
                len(account_campaigns),
                state.STATE_FILE,
            )
            return

        logging.info("Found %d campaign(s) on Facebook NOT in local state:\n", len(orphans))
        for c in orphans:
            logging.info(
                "%s  id=%s  status=%s effective_status=%s  created=%s",
                c.get("name"),
                c["id"],
                c.get("status"),
                c.get("effective_status"),
                c.get("created_time"),
            )
            for adset in c.get("adsets", {}).get("data", []):
                logging.info("  adset %s  id=%s  status=%s", adset.get("name"), adset["id"], adset.get("status"))
                for ad in adset.get("ads", {}).get("data", []):
                    logging.info("    ad %s  id=%s  status=%s", ad.get("name"), ad["id"], ad.get("status"))
            logging.info("")

    elif args.command == "adopt-orphans":
        from datetime import datetime, timedelta, timezone

        from dropship_bot.models import Campaign, Product
        from dropship_bot.store import best_sellers

        existing = state.load_campaigns()
        known_ids = {c.campaign_id for c in existing}
        account_campaigns = facebook_client.list_account_campaigns()
        orphans = [
            c
            for c in account_campaigns
            if c["id"] not in known_ids and c.get("name", "").startswith("Auto - ")
        ]

        if not orphans:
            logging.info("No orphaned 'Auto - ' campaigns found -- nothing to adopt.")
            return

        # Only auto-activate orphans created recently (this run's own crashed
        # launch) -- an older orphan is more likely a stale/duplicate
        # campaign from a previous run that was deliberately left behind,
        # and shouldn't silently start spending again just because it's
        # being adopted into state for visibility.
        activate_cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

        # Match orphan campaign names back to real Shopify products, since a
        # Campaign needs a real Product/ShopifyListing to be useful to
        # monitoring/guardrails -- not just bare Facebook ids.
        shopify_products = {p["title"]: p for p in best_sellers._fetch_active_products()}

        adopted: list[Campaign] = []
        for c in orphans:
            title = c["name"].removeprefix("Auto - ")
            sp = shopify_products.get(title)
            if sp is None:
                logging.warning(
                    "Skipping orphan %r (id=%s): no matching Shopify product found by exact title.",
                    title,
                    c["id"],
                )
                continue

            adsets = c.get("adsets", {}).get("data", [])
            if not adsets:
                logging.warning("Skipping orphan %r (id=%s): no adset found under it.", title, c["id"])
                continue
            adset = adsets[0]
            ads = sorted(adset.get("ads", {}).get("data", []), key=lambda a: a.get("name", ""))
            if not ads:
                logging.warning("Skipping orphan %r (id=%s): no ads found under its adset.", title, c["id"])
                continue

            price = float(sp["variants"][0]["price"]) if sp.get("variants") else 0.0
            product = Product(
                supplier_id=str(sp["id"]),
                title=sp["title"],
                description=sp.get("body_html") or sp["title"],
                supplier_cost_usd=round(price / 3, 2),
                sale_price_usd=price,
                trend_score=50,
                competition_score=50,
                image_urls=[img["src"] for img in sp.get("images", [])],
            )
            campaign = Campaign(
                product=product,
                campaign_id=c["id"],
                adset_id=adset["id"],
                ad_ids=[a["id"] for a in ads],
                daily_budget_usd=config.STARTING_DAILY_BUDGET_USD,
                country=config.TARGET_COUNTRY,
                status="PAUSED",
            )

            created = datetime.fromisoformat(c["created_time"]) if c.get("created_time") else None
            if created is not None and created > activate_cutoff:
                try:
                    facebook_client.set_campaign_status(campaign, "ACTIVE")
                    campaign.last_budget_change_at = datetime.now(timezone.utc)
                    logging.info("Adopted + activated %s (campaign %s)", product.title, campaign.campaign_id)
                except Exception:
                    logging.error(
                        "Adopted %s (campaign %s) but activation failed -- saved with status=%s, "
                        "retry later (e.g. sync-state, or set-budget once fixed).",
                        product.title,
                        campaign.campaign_id,
                        campaign.status,
                        exc_info=True,
                    )
            else:
                logging.info(
                    "Adopted %s (campaign %s) as-is, NOT auto-activated (created %s, older than the "
                    "6-hour cutoff -- likely a stale duplicate from a previous run).",
                    product.title,
                    campaign.campaign_id,
                    c.get("created_time"),
                )

            adopted.append(campaign)
            # Save after every single campaign, not just at the end -- if a
            # later one in this loop fails, everything adopted so far must
            # still land in state instead of getting lost the same way these
            # were lost in the first place.
            state.save_campaigns(existing + adopted)

        logging.info("\nAdopted %d orphaned campaign(s) into state (%s).", len(adopted), state.STATE_FILE)

    elif args.command == "pause-all":
        from datetime import datetime, timezone

        campaigns = state.load_campaigns()
        active = [c for c in campaigns if c.status == "ACTIVE"]
        if not active:
            logging.info("No ACTIVE campaigns in state (%s) -- nothing to do.", state.STATE_FILE)
            return

        for campaign in active:
            logging.info("Pausing %s (%s)", campaign.campaign_id, campaign.product.title)
            facebook_client.set_campaign_status(campaign, "PAUSED")
            campaign.last_budget_change_at = datetime.now(timezone.utc)

        state.save_campaigns(campaigns)
        logging.info("\nPaused %d campaign(s). Spend stops; nothing else in state changed.", len(active))

    elif args.command == "update-targeting-countries":
        drop = {c.strip().upper() for c in args.drop.split(",") if c.strip()}
        new_countries = facebook_client._split_countries(args.countries or config.TARGET_COUNTRY)

        campaigns = state.load_campaigns()
        matching = [
            c for c in campaigns if drop & {code.strip().upper() for code in c.country.split(",")}
        ]
        if not matching:
            logging.info("No campaigns in state currently target %s -- nothing to do.", sorted(drop))
            return

        updated = 0
        for campaign in matching:
            old_country = campaign.country
            try:
                facebook_client.update_adset_countries(campaign, new_countries)
            except Exception:
                # A single stale/deleted adset (e.g. one of the paused
                # campaigns from an earlier test) must not block every other
                # campaign's update -- log it and keep going, same reasoning
                # as the launch-partial-failure fix: never let one failure
                # cost work that already succeeded.
                logging.error(
                    "%s (%s): failed to update targeting, skipping",
                    campaign.campaign_id,
                    campaign.product.title,
                    exc_info=True,
                )
                continue
            updated += 1
            logging.info(
                "%s (%s): %s -> %s", campaign.campaign_id, campaign.product.title, old_country, campaign.country
            )
            # Save after every single campaign, not just at the end -- if a
            # later one fails, everything updated so far must still land in
            # state instead of being lost.
            state.save_campaigns(campaigns)

        logging.info("\nUpdated targeting on %d/%d campaign(s). State saved to %s", updated, len(matching), state.STATE_FILE)

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

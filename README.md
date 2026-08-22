# Automated Dropshipping Pipeline

Research winning products → push them to your Shopify store → generate ad
creative → launch Facebook ad campaigns → monitor performance and
auto-scale/pause based on guardrail rules.

Runs entirely in **test mode** by default: mock product data, no real API
calls, no ad spend. Flip to live mode only once you've set up real accounts
and are ready to spend real money.

## How it works

```
research.trending      -> single entry point; test mode uses mock data, live mode delegates to:
research.aliexpress     -> free Affiliate API query, or CSV import of manually-picked products
store.shopify_client    -> pushes winning products to your Shopify store
store.best_sellers      -> ranks products ALREADY in the store by real sales (or a price heuristic)
store.inspect           -> read-only dump of shop info, shipping zones, products (setup/debug tool)
ads.creative            -> generates ad copy per product (Claude-written if ANTHROPIC_API_KEY is set, else templates)
ads.facebook_client      -> creates the campaign/adset/ad (launches PAUSED)
pipeline.run_launch_cycle              -> research -> Shopify push -> ads, for brand new products
pipeline.run_launch_cycle_for_existing_products -> ads for the store's best existing products, no new research
monitoring.rules        -> pure guardrail decision logic (scale / pause / hold)
monitoring.loop         -> pulls live spend/purchases, applies rules, updates campaigns
pipeline.top_up_campaigns -> after a pause, launches a new untested product to refill the open slot
```

If your store already has a catalog, `launch-existing` is the faster path — it
skips research/product-push entirely and just picks winners from what's
already live (see `python -m dropship_bot.cli launch-existing --help`).
`store.best_sellers` and `store.inspect` are read-only against the real store
regardless of `DROPSHIP_TEST_MODE` — they only look at data that already
exists, so there's nothing unsafe about running them as soon as real Shopify
credentials are set, even before Facebook is configured.

Campaigns always launch **paused** and only go live at
`DROPSHIP_STARTING_DAILY_BUDGET_USD` (default $5/day) — never at an
inflated budget. From there, `monitor` is the only thing allowed to change
budgets, and only within these limits:

- **Daily budget cap** — a campaign's budget never exceeds `DROPSHIP_DAILY_BUDGET_CAP_USD`.
- **Max budget increase per step** — scaling moves are capped at `DROPSHIP_MAX_DAILY_INCREASE_PCT`.
- **Cooldown** — budget changes are at least `DROPSHIP_COOLDOWN_HOURS` apart, no rapid-fire scaling.
- **Kill-switch** — a campaign pauses itself once it has spent `DROPSHIP_PAUSE_SPEND_PCT_NO_SALE`% of its own daily budget with zero purchases (proportional, so it reacts the same at €5/day or €20/day) — but only once it has at least `DROPSHIP_MIN_LINK_CLICKS_BEFORE_KILL` link-clicks, since at small budgets the % threshold alone can mean judging a product on 1-3 unlucky clicks. A hard stop at `DROPSHIP_HARD_STOP_SPEND_PCT_NO_SALE`% still pauses regardless of clicks, so a broken/very-low-CTR ad can't spend forever waiting for a click count it'll never reach. Purchase counts are matched against several known Meta action-types (`omni_purchase`/`purchase`/`offsite_conversion.fb_pixel_purchase`), not just one exact string — if your Pixel/CAPI setup reports under a type not in that list, sales still won't be counted, so verify against a real conversion in Events Manager before trusting the kill-switch.
- **Scale on first signal** — a campaign scales up as soon as it has `DROPSHIP_SCALE_MIN_PURCHASES`+ purchases, unless CPA is already above `DROPSHIP_PAUSE_CPA_USD` (a sale at a terrible cost pauses instead of scaling).
- **Auto-replenish** — every `monitor` run also calls `top_up_campaigns`: if pauses dropped the active count below `DROPSHIP_TARGET_ACTIVE_CAMPAIGNS` (default 3), it launches that many new, previously-untested products (via `store.best_sellers`, excluding every product that already has a campaign — win, lose, or still running) to refill the open slots. Requires `SHOPIFY_LIVE`; skipped with a log line otherwise.

This is a deliberately aggressive, fast-reacting policy suited to small
per-campaign budgets (€5–20/day) — it optimizes for cutting losers and
compounding winners quickly rather than waiting for a large, statistically
comfortable sample. `monitor` needs to run often enough for the % kill-switch
to matter: checking only once a day means a losing campaign can burn its
*entire* daily budget before being caught, not just the configured
percentage — run it every 2–4 hours if you want the threshold to be
meaningful. **This also requires the Meta Pixel / Conversions API to be
installed on the store** (via Shopify's official Facebook & Instagram sales
channel) — without it, Facebook never reports purchases back, `purchases`
stays 0 no matter how many real sales happen, and every campaign eventually
gets killed by the 0-purchase rule regardless of real performance.

All of these are environment variables (see `.env.example`) — tune them to your risk tolerance.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults to test mode, no keys needed yet
```

### Try it now, no accounts needed

```bash
python -m dropship_bot.cli launch      # research -> mock Shopify push -> mock FB campaign launch
python -m dropship_bot.cli monitor     # simulates a day of performance data, applies guardrails
```

Run `monitor` a few times in a row to see campaigns get scaled up, held, or
paused as mock performance data comes in.

### Going live

1. **Shopify**: create a store, then Settings → Apps and sales channels →
   Develop apps → create a custom app with `write_products`,
   `write_inventory`, `read_orders`, `write_orders` scopes. Copy the Admin
   API access token.
2. **AliExpress product sourcing** — two options, pick one:
   - **Affiliate API** (free, real automation): sign up at
     portals.aliexpress.com, create an app in their Open Platform console to
     get an App Key + Secret, set `ALIEXPRESS_APP_KEY`/`ALIEXPRESS_APP_SECRET`/
     `ALIEXPRESS_TRACKING_ID`. `research/aliexpress.py` then queries hot
     products automatically — verify the response parsing against a real
     call before fully trusting it (documented at the top of that file).
     Optionally set `ALIEXPRESS_SEARCH_KEYWORDS` to point it at a specific
     niche (e.g. `fashion accessories jewelry sunglasses handbags`) instead
     of whatever's broadly trending across all of AliExpress.
   - **CSV import** (zero setup, no approval wait): hand-pick a few products
     via AliExpress's own free "Dropshipping Center" or DSers, save them to a
     CSV (columns documented in `research/aliexpress.py`, template at
     `fashion_products.example.csv`), point `ALIEXPRESS_CSV_PATH` at it.
     Research becomes "curate weekly", everything downstream (Shopify push,
     ads, monitoring) stays fully automatic. This step needs a human (or an
     agent with real AliExpress access) — an assistant running in a
     sandboxed environment typically can't reach aliexpress.com directly to
     pick products itself.
   - Either way, install the free **DSers** Shopify app to handle actual
     order fulfillment (routing paid orders to the AliExpress seller) —
     AliExpress has no public API for individuals to place orders
     programmatically, so this piece intentionally isn't part of this
     codebase.
3. **Facebook**: create a Meta Business Manager + ad account, create an app
   at developers.facebook.com with the Marketing API product, generate a
   System User access token with `ads_management`, `ads_read`,
   `business_management`. Note Facebook requires App Review before a token
   gets production access outside your own test account.
4. Fill in `.env` with all of the above and set `DROPSHIP_TEST_MODE=false`.
5. Start with a **small daily budget cap** and watch the first few `monitor`
   runs closely before trusting it unattended.

## Design decisions worth knowing

- **Interest targeting is opt-in-by-default but unverified against a real account** — `launch_campaign` looks up category interests (skincare/hair care/beauty keywords, see `_CATEGORY_INTEREST_QUERIES` in `ads/facebook_client.py`) via Meta's live Ads Targeting Search and adds them on top of geo+age targeting. It fails open (falls back to broad targeting) on any error, but check what it actually picks (logged at launch) before trusting it, and consider `DROPSHIP_INTEREST_TARGETING_ENABLED=false` if scaled/paused outcomes look worse than a broad-targeting baseline — Meta's own delivery algorithm under `OFFSITE_CONVERSIONS` optimization often finds buyers just as well without manual narrowing, especially at small daily budgets.

- **Country default is GB, not US** — same-language creative, much cheaper
  CPMs, cheaper to validate the system before scaling proven winners into
  the more expensive/competitive US market.
- **Every external call has a test-mode branch** — this was built without
  any real credentials, so every module (`shopify_client`, `facebook_client`,
  `trending.fetch_supplier_catalog`) needs its `NotImplementedError`/mock
  branch reviewed against the real API before going live.
- **`monitor` is one pass per call, not a long-running loop** — run it from
  cron/a scheduler so you control cadence and can see failures per-run
  instead of trusting a background process.
- **State is a flat JSON file** (`.dropship_state.json`) — fine for one
  operator on one machine; swap `state.py` for a real database before this
  runs across multiple machines or products at any real scale.

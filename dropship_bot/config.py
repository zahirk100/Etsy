"""Central configuration. All secrets come from environment variables (.env) —
never hardcode keys.

Each integration (Shopify, Facebook, AliExpress) goes live independently as
soon as its own credentials are present — e.g. Shopify store setup can run
for real before Facebook is configured, rather than one missing key forcing
everything into mock mode. DROPSHIP_TEST_MODE is an explicit override for
when you want to force one behavior regardless of what credentials exist:
"true" forces everything mocked (demos, CI), "false" requires every
credential to be present up front (fails loudly on a missing key instead of
silently mocking it) before allowing full live mode.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str) -> bool | None:
    val = os.getenv(name)
    if val is None or val == "":
        return None
    return val.lower() != "false"


SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_ADMIN_API_TOKEN = os.getenv("SHOPIFY_ADMIN_API_TOKEN", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")

FACEBOOK_AD_ACCOUNT_ID = os.getenv("FACEBOOK_AD_ACCOUNT_ID", "")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_API_VERSION = os.getenv("FACEBOOK_API_VERSION", "v21.0")
# The Meta Pixel connected via Shopify's Facebook & Instagram sales channel
# -- required as the adset's "promoted object" so Facebook knows which
# conversion events (purchases) to optimize delivery and report insights for.
FACEBOOK_PIXEL_ID = os.getenv("FACEBOOK_PIXEL_ID", "")

# --- AliExpress (free Affiliate API — portals.aliexpress.com) ---
# Used for automated product research/query. Order fulfillment itself is
# handled separately by the free DSers Shopify app, not by this codebase —
# AliExpress does not offer a public "place order" API for individuals.
ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY", "")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "")

# Fallback/no-API-approval-yet path: manually curated products (picked via
# AliExpress's own free "Dropshipping Center" or DSers) exported to a CSV
# with columns: supplier_id,title,description,supplier_cost_usd,sale_price_usd,
# trend_score,competition_score,image_urls (semicolon-separated).
ALIEXPRESS_CSV_PATH = os.getenv("ALIEXPRESS_CSV_PATH", "")

_shopify_creds_present = bool(SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_API_TOKEN)
_facebook_creds_present = bool(FACEBOOK_AD_ACCOUNT_ID and FACEBOOK_ACCESS_TOKEN)
_aliexpress_creds_present = bool((ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET) or ALIEXPRESS_CSV_PATH)

_explicit_test_mode = _env_bool("DROPSHIP_TEST_MODE")

if _explicit_test_mode is True:
    SHOPIFY_LIVE = FACEBOOK_LIVE = ALIEXPRESS_LIVE = False
elif _explicit_test_mode is False:
    _missing = [
        name
        for name, present in [
            ("Shopify", _shopify_creds_present),
            ("Facebook", _facebook_creds_present),
            ("AliExpress", _aliexpress_creds_present),
        ]
        if not present
    ]
    if _missing:
        raise RuntimeError(
            f"DROPSHIP_TEST_MODE=false but credentials are missing for: {', '.join(_missing)}. "
            "Set them in .env, or leave DROPSHIP_TEST_MODE unset to let each "
            "integration go live independently as its own credentials appear."
        )
    SHOPIFY_LIVE = FACEBOOK_LIVE = ALIEXPRESS_LIVE = True
else:
    SHOPIFY_LIVE = _shopify_creds_present
    FACEBOOK_LIVE = _facebook_creds_present
    ALIEXPRESS_LIVE = _aliexpress_creds_present

# Back-compat / overall status, used only for the CLI banner.
TEST_MODE = not (SHOPIFY_LIVE and FACEBOOK_LIVE)

# Start in a cheaper English-speaking market to validate the system before
# scaling spend into the more expensive/competitive US market.
TARGET_COUNTRY = os.getenv("DROPSHIP_TARGET_COUNTRY", "GB")

# How many campaigns should be actively testing at once. When monitor pauses
# one and the active count drops below this, a new untested product is
# launched to fill the open slot.
TARGET_ACTIVE_CAMPAIGNS = int(os.getenv("DROPSHIP_TARGET_ACTIVE_CAMPAIGNS", "3"))

# --- Guardrails (all overridable via env) ---------------------------------
DAILY_BUDGET_CAP_USD = float(os.getenv("DROPSHIP_DAILY_BUDGET_CAP_USD", "20"))
STARTING_DAILY_BUDGET_USD = float(os.getenv("DROPSHIP_STARTING_DAILY_BUDGET_USD", "5"))
MAX_DAILY_BUDGET_INCREASE_PCT = float(
    os.getenv("DROPSHIP_MAX_DAILY_INCREASE_PCT", "20")
)
BUDGET_CHANGE_COOLDOWN_HOURS = float(os.getenv("DROPSHIP_COOLDOWN_HOURS", "24"))
PAUSE_IF_CPA_ABOVE_USD = float(os.getenv("DROPSHIP_PAUSE_CPA_USD", "25"))
# Kill-switch: pause once spend hits this % of the campaign's OWN daily
# budget with zero purchases (proportional, so it reacts the same whether
# a campaign runs at €5/day or €20/day).
PAUSE_IF_SPEND_PCT_OF_BUDGET_WITH_NO_SALE = float(
    os.getenv("DROPSHIP_PAUSE_SPEND_PCT_NO_SALE", "50")
)
# Scale up as soon as a campaign has at least this many purchases (still
# subject to the CPA pause-check above, the cooldown, and the budget cap).
SCALE_IF_PURCHASES_AT_LEAST = int(os.getenv("DROPSHIP_SCALE_MIN_PURCHASES", "1"))

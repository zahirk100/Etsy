"""Central configuration. All secrets come from environment variables (.env) —
never hardcode keys. TEST_MODE runs the entire pipeline against mock data with
zero real API calls and zero ad spend.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _has_live_credentials() -> bool:
    required = [
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_ADMIN_API_TOKEN",
        "FACEBOOK_AD_ACCOUNT_ID",
        "FACEBOOK_ACCESS_TOKEN",
    ]
    return all(os.getenv(key) for key in required)


# Explicit DROPSHIP_TEST_MODE=false is required to ever go live — missing
# credentials alone should not silently flip this to True.
TEST_MODE = os.getenv("DROPSHIP_TEST_MODE", "true").lower() != "false"
if not TEST_MODE and not _has_live_credentials():
    raise RuntimeError(
        "DROPSHIP_TEST_MODE=false but Shopify/Facebook credentials are missing. "
        "Set them in .env or leave TEST_MODE on."
    )

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "")
SHOPIFY_ADMIN_API_TOKEN = os.getenv("SHOPIFY_ADMIN_API_TOKEN", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")

FACEBOOK_AD_ACCOUNT_ID = os.getenv("FACEBOOK_AD_ACCOUNT_ID", "")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_API_VERSION = os.getenv("FACEBOOK_API_VERSION", "v21.0")

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

# Start in a cheaper English-speaking market to validate the system before
# scaling spend into the more expensive/competitive US market.
TARGET_COUNTRY = os.getenv("DROPSHIP_TARGET_COUNTRY", "GB")

# --- Guardrails (all overridable via env) ---------------------------------
DAILY_BUDGET_CAP_USD = float(os.getenv("DROPSHIP_DAILY_BUDGET_CAP_USD", "20"))
STARTING_DAILY_BUDGET_USD = float(os.getenv("DROPSHIP_STARTING_DAILY_BUDGET_USD", "5"))
MAX_DAILY_BUDGET_INCREASE_PCT = float(
    os.getenv("DROPSHIP_MAX_DAILY_INCREASE_PCT", "20")
)
BUDGET_CHANGE_COOLDOWN_HOURS = float(os.getenv("DROPSHIP_COOLDOWN_HOURS", "24"))
PAUSE_IF_CPA_ABOVE_USD = float(os.getenv("DROPSHIP_PAUSE_CPA_USD", "25"))
SCALE_IF_ROAS_ABOVE = float(os.getenv("DROPSHIP_SCALE_ROAS", "2.0"))
MIN_SPEND_BEFORE_JUDGING_USD = float(os.getenv("DROPSHIP_MIN_SPEND_JUDGE_USD", "15"))

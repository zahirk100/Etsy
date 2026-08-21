"""Read-only Shopify store inspection — run this once after wiring up real
credentials to see what's actually in the store (shop info, currency,
shipping zones, existing products) before pushing anything.

    python -m dropship_bot.store.inspect
"""
import requests

from dropship_bot import config


def _get(path: str, params: dict | None = None) -> dict:
    url = f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"
    resp = requests.get(
        url,
        params=params or {},
        headers={"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    if not (config.SHOPIFY_STORE_DOMAIN and config.SHOPIFY_ADMIN_API_TOKEN):
        print("SHOPIFY_STORE_DOMAIN / SHOPIFY_ADMIN_API_TOKEN not set in .env")
        return

    shop = _get("shop.json")["shop"]
    print("=== Shop ===")
    print(f"  Name:      {shop['name']}")
    print(f"  Domain:    {shop['domain']} (myshopify: {shop['myshopify_domain']})")
    print(f"  Currency:  {shop['currency']}")
    print(f"  Country:   {shop['country_name']} ({shop['country_code']})")
    print(f"  Timezone:  {shop['iana_timezone']}")
    print(f"  Plan:      {shop['plan_name']}")

    print("\n=== Shipping zones ===")
    zones = _get("shipping_zones.json")["shipping_zones"]
    if not zones:
        print("  (none configured)")
    for zone in zones:
        countries = ", ".join(c["name"] for c in zone.get("countries", [])) or "(no countries)"
        print(f"  - {zone['name']}: {countries}")
        for rate in zone.get("price_based_shipping_rates", []):
            print(f"      rate: {rate['name']} = {rate['price']}")

    print("\n=== Existing products (first 50) ===")
    products = _get("products.json", {"limit": 50})["products"]
    if not products:
        print("  (no products yet)")
    for p in products:
        price = p["variants"][0]["price"] if p.get("variants") else "?"
        print(f"  - [{p['status']}] {p['title']} — {price} {shop['currency']}")


if __name__ == "__main__":
    main()

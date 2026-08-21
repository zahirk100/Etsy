"""Push winning products to Shopify via the Admin REST API.

Docs: https://shopify.dev/docs/api/admin-rest/latest/resources/product
"""
import logging

import requests

from dropship_bot import config
from dropship_bot.models import Product, ShopifyListing

log = logging.getLogger(__name__)


def _api_url(path: str) -> str:
    return f"https://{config.SHOPIFY_STORE_DOMAIN}/admin/api/{config.SHOPIFY_API_VERSION}/{path}"


def push_product(product: Product) -> ShopifyListing:
    if config.TEST_MODE:
        fake_id = f"test-{abs(hash(product.supplier_id)) % 100000}"
        log.info(
            "[TEST MODE] Would create Shopify product: '%s' at $%.2f (cost $%.2f, margin $%.2f)",
            product.title,
            product.sale_price_usd,
            product.supplier_cost_usd,
            product.margin_usd,
        )
        return ShopifyListing(
            product=product,
            shopify_product_id=fake_id,
            product_url=f"https://example-test-store.myshopify.com/products/{fake_id}",
        )

    payload = {
        "product": {
            "title": product.title,
            "body_html": product.description,
            "images": [{"src": url} for url in product.image_urls],
            "variants": [
                {
                    "price": str(product.sale_price_usd),
                    "inventory_management": "shopify",
                    "inventory_quantity": 100,
                }
            ],
            "status": "active",
        }
    }
    resp = requests.post(
        _api_url("products.json"),
        json=payload,
        headers={"X-Shopify-Access-Token": config.SHOPIFY_ADMIN_API_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["product"]
    return ShopifyListing(
        product=product,
        shopify_product_id=str(data["id"]),
        product_url=f"https://{config.SHOPIFY_STORE_DOMAIN}/products/{data['handle']}",
    )


def push_products(products: list[Product]) -> list[ShopifyListing]:
    return [push_product(p) for p in products]

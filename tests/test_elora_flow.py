"""End-to-end test of the live code paths against a fake Shopify + Meta API.

Run: python -m pytest tests/ -q   (or: python tests/test_elora_flow.py)

Patches requests.get/post, so nothing leaves the machine and no money is
spent. Covers: Shopify client-credentials exchange, REST pagination,
availability filtering, public-domain ad links, EUR/JPY budget units,
Instagram identity, lifetime insights, the new guardrail rules, the total
budget cap, token checks and check-setup.
"""
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeResp:
    def __init__(self, data, status=200, links=None, url=""):
        self._data, self.status_code, self.links, self.url = data, status, links or {}, url
        self.text = json.dumps(data)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class World:
    def __init__(self, currency="USD", token_exp_days=None, token_valid=True):
        self.calls = []
        self.currency = currency
        self.token_exp_days = token_exp_days
        self.token_valid = token_valid
        self.insights = {}
        self.n = 1000

    def products(self):
        out = []
        for i in range(300):
            out.append({
                "id": 9000 + i, "title": f"Dress {i}", "handle": f"dress-{i}", "body_html": "<p>Flowy maxi.</p>",
                "images": [{"src": f"https://cdn/x{i}.jpg"}] if i != 1 else [],
                "variants": [{"price": str(20 + i % 60), "inventory_management": "shopify" if i == 2 else None,
                              "inventory_policy": "deny", "inventory_quantity": 0}],
            })
        return out

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        u = urllib.parse.urlparse(url)
        if "myshopify.com" in u.netloc:
            assert headers["X-Shopify-Access-Token"] == "shpat_exchanged"
            if u.path.endswith("products.json"):
                q = dict(urllib.parse.parse_qsl(u.query))
                prods = self.products()
                if q.get("page_info") == "p2":
                    return FakeResp({"products": prods[250:]})
                return FakeResp({"products": prods[:250]}, links={"next": {"url": url + "?page_info=p2&limit=250"}})
            if u.path.endswith("orders.json"):
                return FakeResp({"orders": [{"id": 1, "line_items": [{"product_id": 9277, "quantity": 2, "price": "50"}]}]})
        path = u.path.split("/", 2)[2]
        if path == "debug_token":
            exp = int((datetime.now(timezone.utc) + timedelta(days=self.token_exp_days)).timestamp()) if self.token_exp_days else 0
            return FakeResp({"data": {"is_valid": self.token_valid, "expires_at": exp, "type": "SYSTEM_USER",
                                      "scopes": ["ads_management", "ads_read", "business_management"]}})
        if path == "act_123":
            return FakeResp({"currency": self.currency, "name": "Elora US", "account_id": "123", "account_status": 1,
                             "timezone_name": "America/New_York"})
        if path == "me/accounts":
            return FakeResp({"data": [{"id": "555", "name": "Elora Moda", "instagram_business_account": {"id": "777", "username": "eloramoda"}}]})
        if path == "act_123/adspixels":
            return FakeResp({"data": [{"id": "333", "name": "Elora Pixel"}]})
        if path.endswith("/insights"):
            cid = path.split("/")[0]
            assert params["date_preset"] == "maximum"
            return FakeResp({"data": [self.insights[cid]] if cid in self.insights else []})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data))
        if url.endswith("/admin/oauth/access_token"):
            assert data["grant_type"] == "client_credentials" and data["client_secret"] == "sec"
            return FakeResp({"access_token": "shpat_exchanged", "expires_in": 86399})
        self.n += 1
        return FakeResp({"id": f"x{self.n}", "success": True})


def load(world, **env):
    base = {
        "DROPSHIP_TEST_MODE": "", "SHOPIFY_STORE_DOMAIN": "r5tp3i-bx.myshopify.com",
        "SHOPIFY_PUBLIC_DOMAIN": "elora-moda.com", "SHOPIFY_CLIENT_ID": "cid", "SHOPIFY_CLIENT_SECRET": "sec",
        "SHOPIFY_ADMIN_API_TOKEN": "", "FACEBOOK_AD_ACCOUNT_ID": "123", "FACEBOOK_ACCESS_TOKEN": "EAAx",
        "FACEBOOK_PAGE_ID": "555", "FACEBOOK_PIXEL_ID": "333", "INSTAGRAM_USER_ID": "777", "ANTHROPIC_API_KEY": "",
        "DROPSHIP_STARTING_DAILY_BUDGET_USD": "", "DROPSHIP_MAX_TOTAL_DAILY_BUDGET_USD": "",
    }
    base.update(env)
    os.environ.update(base)
    import requests

    requests.get, requests.post = world.get, world.post
    mods = [m for m in list(sys.modules) if m.startswith("dropship_bot")]
    for m in mods:
        del sys.modules[m]
    import dropship_bot.config as config  # noqa: F401  (import triggers the token exchange)
    from dropship_bot import pipeline, state
    from dropship_bot.ads import facebook_client
    from dropship_bot.monitoring import loop, rules
    from dropship_bot.store import best_sellers

    return config, pipeline, state, facebook_client, loop, rules, best_sellers


def posts(world, suffix):
    return [c[2] for c in world.calls if c[0] == "POST" and c[1].endswith(suffix)]


def test_launch_existing_live_paths():
    w = World(currency="EUR")
    config, pipeline, state, fb, loop, rules, bs = load(w)
    assert config.FACEBOOK_AD_ACCOUNT_ID == "act_123" and config.SHOPIFY_ADMIN_API_TOKEN == "shpat_exchanged"
    assert config.TARGET_COUNTRY == "US" and config.FACEBOOK_API_VERSION == "v27.0"
    assert config.STARTING_DAILY_BUDGET_USD == 10.0  # empty env var -> default, no crash

    prods = bs._fetch_active_products()
    assert len(prods) == 300  # pagination followed past 250
    picks = bs.pick_products_to_advertise(top_n=2)
    titles = [p.title for p, _ in picks]
    assert titles[0] == "Dress 277"  # ranked by real sales
    assert all(l.product_url.startswith("https://elora-moda.com/products/") for _, l in picks)
    ids = {p.supplier_id for p, _ in bs.pick_products_to_advertise(top_n=400)}
    assert "9001" not in ids and "9002" not in ids  # no image / sold out skipped

    campaigns = pipeline.run_launch_cycle_for_existing_products(top_n=1)
    assert len(campaigns) == 1 and campaigns[0].status == "ACTIVE"
    adset = posts(w, "act_123/adsets")[0]
    assert adset["daily_budget"] == 1000  # EUR cents
    t = json.loads(adset["targeting"])
    assert t["geo_locations"]["countries"] == ["US"] and "flexible_spec" not in t
    cr = json.loads(posts(w, "act_123/adcreatives")[0]["object_story_spec"])
    assert cr["instagram_user_id"] == "777" and cr["page_id"] == "555"
    assert "elora-moda.com/products/dress-277" in cr["link_data"]["link"]
    msg = " ".join(json.loads(p["object_story_spec"])["link_data"]["message"] for p in posts(w, "act_123/adcreatives"))
    assert "free shipping" not in msg.lower()  # no unverified promises in template copy
    print("launch ok")


def test_zero_decimal_currency():
    w = World(currency="JPY")
    config, pipeline, *_ = load(w, DROPSHIP_STARTING_DAILY_BUDGET_USD="1500")
    pipeline.run_launch_cycle_for_existing_products(top_n=1)
    assert posts(w, "act_123/adsets")[0]["daily_budget"] == 1500


def _camp(models, cid, budget=10.0, status="ACTIVE", changed_hours_ago=48):
    p = models.Product("1", "Dress", "d", 10, 40, 50, 50, ["https://cdn/a.jpg"])
    return models.Campaign(p, cid, "s" + cid, ["a" + cid], budget, "US", status,
                           datetime.now(timezone.utc) - timedelta(hours=changed_hours_ago))


def test_rules_lifetime():
    w = World()
    config, pipeline, state, fb, loop, rules, bs = load(w)
    from dropship_bot import models

    I = lambda spend, purch, clicks, rev=0: models.CampaignInsights("c", spend, purch, rev, clicks)
    c = _camp(models, "c1")
    A = rules.Action
    assert rules.decide(c, I(9, 0, 3)).action == A.NONE            # one day at $10: keep testing
    assert rules.decide(c, I(26, 0, 20)).action == A.PAUSE          # fair test, no sale
    assert rules.decide(c, I(30, 0, 5)).action == A.NONE            # too few clicks yet
    assert rules.decide(c, I(41, 0, 5)).action == A.PAUSE           # hard stop
    assert rules.decide(c, I(35, 1, 30, 50)).action == A.NONE       # CPA $35 > 30 but spend < 45: hold
    assert rules.decide(c, I(50, 1, 30, 50)).action == A.PAUSE      # CPA $50 after enough spend
    d = rules.decide(c, I(20, 2, 30, 80))
    assert d.action == A.SCALE_UP and d.new_daily_budget_usd == 12.0
    d = rules.decide(c, I(20, 2, 30, 80), total_active_budget_usd=49)
    assert d.action == A.SCALE_UP and d.new_daily_budget_usd == 11.0  # total cap 50
    d = rules.decide(c, I(20, 2, 30, 80), total_active_budget_usd=50)
    assert d.action == A.NONE
    assert rules.decide(_camp(models, "c2", changed_hours_ago=2), I(20, 2, 30)).action == A.HOLD_COOLDOWN
    print("rules ok")


def test_monitor_and_topup_respect_total_cap():
    w = World()
    config, pipeline, state, fb, loop, rules, bs = load(w, DROPSHIP_MAX_TOTAL_DAILY_BUDGET_USD="25")
    from dropship_bot import models

    camps = [_camp(models, "k1"), _camp(models, "k2")]
    w.insights = {"k1": {"spend": "45", "actions": [{"action_type": "link_click", "value": "40"}]},
                  "k2": {"spend": "5", "actions": []}}
    res = loop.run_once(camps)
    assert [r["action"] for r in res] == ["pause", "none"]
    assert camps[0].status == "PAUSED"
    # 1 active at $10, cap $25 -> room for exactly one more $10 campaign, not two
    new = pipeline.top_up_campaigns(camps)
    assert len(new) == 1
    print("monitor ok")


def test_token_guard_and_check_setup():
    w = World(token_exp_days=3)
    load(w)
    from dropship_bot import cli

    try:
        cli._require_healthy_token()
        raise AssertionError("should have failed")
    except SystemExit as e:
        assert "expires in" in str(e)
    w2 = World(token_valid=False)
    load(w2)
    from dropship_bot import cli as cli2

    try:
        cli2._require_healthy_token()
        raise AssertionError("should have failed")
    except SystemExit as e:
        assert "INVALID or EXPIRED" in str(e)
    w3 = World()
    load(w3)
    from dropship_bot import cli as cli3

    cli3._check_setup()  # all good -> no SystemExit
    print("token/check-setup ok")


if __name__ == "__main__":
    test_launch_existing_live_paths()
    test_zero_decimal_currency()
    test_rules_lifetime()
    test_monitor_and_topup_respect_total_cap()
    test_token_guard_and_check_setup()
    print("ALL PASSED")

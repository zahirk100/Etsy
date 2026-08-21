"""Shared data structures passed between pipeline stages."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Product:
    supplier_id: str
    title: str
    description: str
    supplier_cost_usd: float
    sale_price_usd: float
    trend_score: float          # 0-100, higher = more upward search/order momentum
    competition_score: float    # 0-100, higher = more saturated
    image_urls: list[str] = field(default_factory=list)

    @property
    def margin_usd(self) -> float:
        return round(self.sale_price_usd - self.supplier_cost_usd, 2)

    @property
    def opportunity_score(self) -> float:
        """Simple heuristic: reward trend momentum, penalize saturation."""
        return round(self.trend_score - 0.5 * self.competition_score, 2)


@dataclass
class ShopifyListing:
    product: Product
    shopify_product_id: str
    product_url: str


@dataclass
class AdCreative:
    product: Product
    primary_text: str
    headline: str
    description: str


@dataclass
class Campaign:
    product: Product
    campaign_id: str
    adset_id: str
    ad_id: str
    daily_budget_usd: float
    country: str
    status: str = "ACTIVE"
    last_budget_change_at: datetime | None = None


@dataclass
class CampaignInsights:
    campaign_id: str
    spend_usd: float
    purchases: int
    revenue_usd: float

    @property
    def cpa_usd(self) -> float | None:
        if self.purchases == 0:
            return None
        return round(self.spend_usd / self.purchases, 2)

    @property
    def roas(self) -> float | None:
        if self.spend_usd == 0:
            return None
        return round(self.revenue_usd / self.spend_usd, 2)

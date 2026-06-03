import logging
from sqlalchemy import select, update

from database import AsyncSessionLocal
from models.product import Product
from models.competitor_analysis import CompetitorAnalysis
from models.pricing_rule import PricingRule, PriceChangeLog

log = logging.getLogger(__name__)


def compute_recommendation(
    competitors: list,
    rule: PricingRule,
    current_price: float | None,
) -> tuple[float | None, float | None, str, float, bool]:
    """
    Returns (market_price, recommended_price, reason, deviation_pct, should_change).
    Returns (None, None, reason, 0.0, False) when no competitor data is available.
    """
    prices = sorted(c.price for c in competitors if c.price and c.price > 0)
    if not prices:
        return None, None, "Нет данных о конкурентах", 0.0, False

    if rule.target_position == "equal_top_1":
        market_price = prices[0]
        label = f"лучшая цена рынка ({prices[0]:,.0f} ₽)"
    elif rule.target_position == "below_top_3":
        top = prices[:3]
        market_price = sum(top) / len(top)
        label = f"среднее топ-{len(top)} конкурентов ({market_price:,.0f} ₽)"
    else:  # custom
        market_price = sum(prices) / len(prices)
        label = f"средняя рыночная цена ({market_price:,.0f} ₽)"

    raw = market_price * (1.0 - rule.target_percent / 100.0)
    recommended = round(max(rule.min_price, min(rule.max_price, raw)), 2)

    direction = "ниже" if rule.target_percent >= 0 else "выше"
    pct_abs = abs(rule.target_percent)
    reason = f"{label.capitalize()}, на {pct_abs}% {direction} → {recommended:,.2f} ₽"

    if current_price and current_price > 0:
        deviation = abs(current_price - recommended) / current_price * 100.0
    else:
        deviation = 0.0

    should_change = deviation > rule.reaction_threshold
    return market_price, recommended, reason, round(deviation, 2), should_change


async def check_pricing(product_id: str) -> None:
    """Scheduled background task: re-evaluates pricing rule and auto-applies if needed."""
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(select(Product).where(Product.id == product_id))
            product = result.scalar_one_or_none()
            if not product:
                return

            result = await db.execute(
                select(PricingRule).where(PricingRule.product_id == product_id)
            )
            rule = result.scalar_one_or_none()
            if not rule or not rule.auto_mode:
                return

            result = await db.execute(
                select(CompetitorAnalysis).where(CompetitorAnalysis.product_id == product_id)
            )
            competitors = list(result.scalars().all())

            _, recommended, reason, _, should_change = compute_recommendation(
                competitors, rule, product.price
            )

            if not should_change or recommended is None:
                return

            old_price = product.price or 0.0
            await db.execute(
                update(Product).where(Product.id == product_id).values(price=recommended)
            )
            db.add(PriceChangeLog(
                product_id=product_id,
                old_price=old_price,
                new_price=recommended,
                reason=reason,
                source="auto",
            ))
            await db.commit()
            log.info("check_pricing: %s → %.2f (was %.2f)", product_id, recommended, old_price)

        except Exception as exc:
            log.error("check_pricing: product %s failed: %s", product_id, exc)

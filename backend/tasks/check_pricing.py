import logging
from sqlalchemy import select, update

from database import AsyncSessionLocal
from models.product import Product
from models.pricing_rule import PricingRule, PriceChangeLog

log = logging.getLogger(__name__)
UNVERIFIED_COMPETITOR_REASON = "Недостаточно подтверждённых данных о конкурентах"


def compute_recommendation(
    competitors: list,
    rule: PricingRule,
    current_price: float | None,
) -> tuple[float | None, float | None, str, float, bool]:
    """
    Retired legacy recommendation seam; no trusted observation source exists.

    Historical rows have no provenance discriminator. Do not inspect their prices,
    infer provenance, or return a recommendation even for direct/internal callers.
    Preserve the tuple contract until a separately reviewed verified source exists.
    """
    return None, None, UNVERIFIED_COMPETITOR_REASON, 0.0, False


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

            _, recommended, reason, _, should_change = compute_recommendation(
                [], rule, product.price
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

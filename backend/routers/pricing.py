import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.product import Product
from models.competitor_analysis import CompetitorAnalysis
from models.pricing_rule import PricingRule, PriceChangeLog
from schemas.pricing import (
    PricingRuleUpsert, PricingRuleOut, PriceChangeLogOut, PriceCheckResult,
)
from tasks.check_pricing import compute_recommendation
from services.marketplace import executor

log    = logging.getLogger(__name__)
router = APIRouter()


async def _get_product_or_404(product_id: str, user_id: str, db: AsyncSession) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return product


async def _get_rule_or_404(product_id: str, db: AsyncSession) -> PricingRule:
    result = await db.execute(
        select(PricingRule).where(PricingRule.product_id == product_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило ценообразования не настроено")
    return rule


# ── GET rule ──────────────────────────────────────────────────────────────────

@router.get("/products/{product_id}/pricing-rule", response_model=PricingRuleOut)
async def get_rule(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_product_or_404(product_id, current_user.id, db)
    return await _get_rule_or_404(product_id, db)


# ── PUT rule (upsert) ─────────────────────────────────────────────────────────

@router.put("/products/{product_id}/pricing-rule", response_model=PricingRuleOut)
async def upsert_rule(
    product_id: str,
    data: PricingRuleUpsert,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_product_or_404(product_id, current_user.id, db)

    result = await db.execute(
        select(PricingRule).where(PricingRule.product_id == product_id)
    )
    rule = result.scalar_one_or_none()

    if rule:
        for k, v in data.model_dump().items():
            setattr(rule, k, v)
    else:
        rule = PricingRule(product_id=product_id, **data.model_dump())
        db.add(rule)

    await db.commit()
    await db.refresh(rule)
    return rule


# ── GET history ───────────────────────────────────────────────────────────────

@router.get("/products/{product_id}/price-history", response_model=List[PriceChangeLogOut])
async def get_history(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_product_or_404(product_id, current_user.id, db)
    result = await db.execute(
        select(PriceChangeLog)
        .where(PriceChangeLog.product_id == product_id)
        .order_by(PriceChangeLog.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()


# ── POST check ────────────────────────────────────────────────────────────────

@router.post("/products/{product_id}/price-check", response_model=PriceCheckResult)
async def check_price(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await _get_product_or_404(product_id, current_user.id, db)
    rule = await _get_rule_or_404(product_id, db)

    result = await db.execute(
        select(CompetitorAnalysis).where(CompetitorAnalysis.product_id == product_id)
    )
    competitors = list(result.scalars().all())

    market_price, recommended, reason, deviation, should_change = compute_recommendation(
        competitors, rule, product.price
    )

    if market_price is None:
        raise HTTPException(status_code=422, detail=reason)

    auto_applied = False
    if rule.auto_mode and should_change and product.sku:
        old_price = product.price or 0.0
        # L4 — real automated push through the SAME executor. Guard enforces the
        # rule's price floor/ceiling. No local-only imitation.
        res = await executor.execute(
            db=db,
            user_id=current_user.id,
            action_type="set_price",
            payload={
                "marketplace": product.marketplace,
                "offer_id": product.sku,
                "price": recommended,
                "old_price": old_price,
            },
            mode="automated_l4",
            insight_key="margin_crisis",
            idempotency_key=f"price:{product_id}:{recommended}",
            rule={"enabled": True, "guard": {
                "min_price": rule.min_price, "max_price": rule.max_price,
            }},
        )
        if res.ok:
            await db.execute(
                update(Product).where(Product.id == product_id).values(price=recommended)
            )
            db.add(PriceChangeLog(
                product_id=product_id, old_price=old_price, new_price=recommended,
                reason=reason, source="auto",
            ))
            await db.commit()
            auto_applied = True
            log.info("price_pushed: auto user=%s product=%s %.2f→%.2f log=%s",
                     current_user.id, product_id, old_price, recommended, res.log_id)
        else:
            log.warning("auto price push rejected: product=%s err=%s",
                        product_id, res.error)

    return PriceCheckResult(
        market_price=round(market_price, 2),
        recommended_price=recommended,
        reason=reason,
        deviation_percent=deviation,
        should_change=should_change,
        auto_applied=auto_applied,
    )


# ── POST apply ────────────────────────────────────────────────────────────────

@router.post("/products/{product_id}/price-apply", response_model=PriceChangeLogOut)
async def apply_price(
    product_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await _get_product_or_404(product_id, current_user.id, db)
    rule = await _get_rule_or_404(product_id, db)

    result = await db.execute(
        select(CompetitorAnalysis).where(CompetitorAnalysis.product_id == product_id)
    )
    competitors = list(result.scalars().all())

    market_price, recommended, reason, _, _ = compute_recommendation(
        competitors, rule, product.price
    )

    if market_price is None:
        raise HTTPException(status_code=422, detail=reason)

    old_price = product.price or 0.0

    if not product.sku:
        raise HTTPException(409, "У товара нет SKU/артикула — нельзя отправить цену в маркетплейс")

    # L3 — push the price to the real marketplace through the single executor.
    # The local Product.price + PriceChangeLog are updated ONLY on API success
    # (no more local-only imitation).
    res = await executor.execute(
        db=db,
        user_id=current_user.id,
        action_type="set_price",
        payload={
            "marketplace": product.marketplace,
            "offer_id": product.sku,
            "price": recommended,
            "old_price": old_price,
            "step_pct": (abs(recommended - old_price) / old_price * 100.0) if old_price else None,
        },
        mode="manual_l3",
        insight_key="margin_crisis",
        idempotency_key=f"price:{product_id}:{recommended}",
    )
    if not res.ok:
        raise HTTPException(
            status_code=502,
            detail={"message": "Цена не отправлена в маркетплейс", "error": res.error,
                    "log_id": res.log_id},
        )

    await db.execute(
        update(Product).where(Product.id == product_id).values(price=recommended)
    )
    entry = PriceChangeLog(
        product_id=product_id,
        old_price=old_price,
        new_price=recommended,
        reason=reason,
        source="executed",          # real marketplace push, not local-only
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    log.info(
        "price_pushed: user=%s product=%s %.2f→%.2f log=%s",
        current_user.id, product_id, old_price, recommended, res.log_id,
    )
    return entry

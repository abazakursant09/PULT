"""
Insight → Action mapping (ME-6). Turns an Action Engine insight_key into an
execution plan (action_type + payload) for the SHARED executor. This is a
DECISION layer: it builds plans, it never calls a marketplace client and never
executes — that is exclusively Executor.execute().

The same plan a user runs at L3 is reused by L4 automation (one code path).

insight_key format: "<itype>:<marketplace>:<sku>".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.product import Product
from models.pricing_rule import PricingRule
from models.competitor_analysis import CompetitorAnalysis
from tasks.check_pricing import compute_recommendation

SUPPORTED = {"margin_crisis", "high_ad_spend", "seo_opportunity", "rating_good"}


@dataclass
class Plan:
    insight_key: str
    itype: str
    action_type: str | None = None
    payload: dict = field(default_factory=dict)
    automation_eligible: bool = False
    batch: bool = False                     # rating_good publishes many reviews
    needs_input: list[str] = field(default_factory=list)
    descriptor: dict = field(default_factory=dict)  # reason / action / what / effect for UI

    @property
    def ready(self) -> bool:
        return self.action_type is not None and not self.needs_input


def parse_key(insight_key: str) -> tuple[str, str | None, str | None]:
    parts = insight_key.split(":")
    itype = parts[0]
    mp = parts[1] if len(parts) > 1 else None
    sku = parts[2] if len(parts) > 2 else None
    return itype, mp, sku


async def _resolve_product(db: AsyncSession, user_id: str, mp: str | None, sku: str | None):
    if not (mp and sku):
        return None
    return (
        await db.execute(
            select(Product).where(
                Product.user_id == user_id,
                Product.marketplace == mp,
                Product.sku == sku,
            )
        )
    ).scalars().first()


async def resolve_plan(
    db: AsyncSession, user_id: str, insight_key: str, overrides: dict | None = None
) -> Plan:
    overrides = overrides or {}
    itype, mp, sku = parse_key(insight_key)
    plan = Plan(insight_key=insight_key, itype=itype)

    if itype not in SUPPORTED:
        plan.descriptor = {"reason": f"insight '{itype}' has no execution mapping yet"}
        plan.needs_input = ["unsupported_insight"]
        return plan

    # ── margin_crisis → raise price toward the recommended level ──────────────
    if itype == "margin_crisis":
        product = await _resolve_product(db, user_id, mp, sku)
        if not product or not product.sku:
            plan.needs_input = ["product"]
            return plan
        rule = (await db.execute(
            select(PricingRule).where(PricingRule.product_id == product.id)
        )).scalars().first()
        competitors = (await db.execute(
            select(CompetitorAnalysis).where(CompetitorAnalysis.product_id == product.id)
        )).scalars().all()
        recommended = overrides.get("price")
        if recommended is None:
            if not rule:
                plan.needs_input = ["price"]  # no rule to compute from
                return plan
            _, recommended, _reason, _, _ = compute_recommendation(
                list(competitors), rule, product.price
            )
            if recommended is None:
                plan.needs_input = ["price"]
                return plan
        plan.action_type = "set_price"
        plan.payload = {"marketplace": mp, "offer_id": product.sku,
                        "price": recommended, "old_price": product.price or 0.0}
        plan.automation_eligible = True
        plan.descriptor = {
            "reason": "Маржа под давлением",
            "action": "Поднять цену к рекомендованному уровню",
            "what_will_happen": f"Цена {product.sku} -> {recommended} в {mp}",
            "expected_effect": "Восстановление маржинальности",
        }
        return plan

    # ── high_ad_spend → cut the bid / pause the campaign ──────────────────────
    if itype == "high_ad_spend":
        campaign_id = overrides.get("campaign_id")
        if campaign_id is None:
            # ad campaigns are not modeled yet — UI must supply the campaign id
            plan.needs_input = ["campaign_id", "cpm_or_action"]
            plan.descriptor = {
                "reason": "Реклама съедает маржу (высокий ACOS)",
                "action": "Снизить ставку или поставить кампанию на паузу",
                "what_will_happen": "Ставка/состояние кампании изменится в кабинете",
                "expected_effect": "Снижение рекламных потерь",
            }
            return plan
        if overrides.get("action") == "pause":
            plan.action_type = "ad_set_state"
            plan.payload = {"marketplace": mp, "campaign_id": campaign_id, "action": "pause"}
        else:
            plan.action_type = "ad_set_bid"
            plan.payload = {"marketplace": mp, "campaign_id": campaign_id,
                            "cpm": overrides["cpm"], "adv_type": overrides.get("adv_type", 8),
                            "old_cpm": overrides.get("old_cpm")}
        plan.automation_eligible = True
        return plan

    # ── seo_opportunity → apply a card update ─────────────────────────────────
    if itype == "seo_opportunity":
        card = overrides.get("card")
        if not card:
            # structured card changes are not stored on the insight — UI supplies them
            plan.needs_input = ["card"]
            plan.descriptor = {
                "reason": "Карточка ограничивает CTR/выдачу",
                "action": "Применить SEO-изменения к карточке",
                "what_will_happen": "Обновление title/описания/характеристик в кабинете",
                "expected_effect": "Рост видимости и CTR",
            }
            return plan
        plan.action_type = "update_card"
        plan.payload = {"marketplace": mp, "offer_id": sku, "card": card,
                        "old_card": overrides.get("old_card")}
        plan.automation_eligible = True
        return plan

    # ── rating_good → publish prepared positive review answers (batch) ────────
    if itype == "rating_good":
        plan.action_type = "publish_review_response"
        plan.batch = True
        plan.automation_eligible = True
        plan.descriptor = {
            "reason": "Есть положительные отзывы без ответа",
            "action": "Опубликовать подготовленные ответы",
            "what_will_happen": "Ответы на позитивные отзывы публикуются в кабинете",
            "expected_effect": "Рост вовлечённости и рейтинга",
        }
        return plan

    return plan

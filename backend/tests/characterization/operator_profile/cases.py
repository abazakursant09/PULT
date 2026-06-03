"""Characterization fixtures for logic.operator_profile (Sprint 71). Observe-only.

Hand-built: load_profile expects OperatorDecision-like records (dicts work);
adapt_insight / apply_adaptations need a real OperatorProfile (the Duck stand-in
has no count methods).
"""
from logic.operator_profile import (
    adapt_insight, apply_adaptations, load_profile,
)
from characterization._engine import call, insight, jsonable

_DECISIONS = [
    {"insight_type": "seo_opportunity", "accepted": True, "resolved_after_days": 20},
    {"insight_type": "seo_opportunity", "accepted": True, "resolved_after_days": 18},
    {"insight_type": "high_ad_spend", "ignored": True},
    {"insight_type": "high_ad_spend", "ignored": True},
    {"insight_type": "high_ad_spend", "ignored": True},
    {"insight_type": "high_ad_spend", "ignored": True},
]


def build_cases():
    c = {}
    c["load_profile.empty"] = call(load_profile, [])
    c["load_profile.populated"] = call(load_profile, _DECISIONS)

    empty_profile = load_profile([])
    full_profile = load_profile(_DECISIONS)

    recs = ["Запустить авто-пересборку карточки", "Проверить цену"]
    c["adapt_insight.empty_profile"] = call(adapt_insight, "seo_opportunity", recs, 70, empty_profile)
    c["adapt_insight.seo_slow"] = call(adapt_insight, "seo_opportunity", recs, 70, full_profile)
    c["adapt_insight.ad_ignored"] = call(adapt_insight, "high_ad_spend", recs, 70, full_profile)

    # apply_adaptations mutates in-place; freeze resulting fields.
    ins = [insight(key="seo_opportunity:wildberries:A",
                   recommendations=["Запустить авто-пересборку карточки", "Проверить цену"],
                   confidence=70, is_demo=False, is_secondary=False)]
    apply_adaptations(ins, full_profile)
    c["apply_adaptations.effect"] = jsonable([
        {"key": i.key, "recommendations": i.recommendations,
         "confidence": i.confidence, "adaptation_note": i.adaptation_note}
        for i in ins
    ])
    return c

"""
Legal Signal Builder (Legal A5/A6) — deterministic, template-only.

Turns a DETECTED LegalRuleEvaluationResult into the advisory fields of a
legal_signal following the PULT 5-part doctrine (what_happened / why_it_matters /
meaning / recommended_action / expected_effect) + a canonical advisory action.
No AI, no language-model use, no forecast, no money, no legal conclusion, no
asserted compliance. expected_effect is QUALITATIVE risk-reduction only.
"""
from __future__ import annotations

import json
from typing import Mapping

from .snapshot import LegalSnapshot

# requirement → legal domain category (A2 legal_finding.category)
CATEGORY = {
    "product_certification": "certification",
    "trademark_usage": "ip",
    "labeling_requirements": "labeling",
    "marketplace_offer_terms": "content",
    "return_policy_obligations": "content",
    "content_claim_risk": "content",
}

# requirement → inherent risk kind (finding.estimated_effect_type)
EFFECT_KIND = {
    "product_certification": "block_risk",
    "trademark_usage": "takedown_risk",
    "labeling_requirements": "fine_risk",
    "marketplace_offer_terms": "compliance_risk",
    "return_policy_obligations": "compliance_risk",
    "content_claim_risk": "takedown_risk",
}

# qualitative expected effect — NO money, NO forecast, NO guarantee
_EFFECT_TEXT = {
    "compliance_risk": "может снизить риск претензий по соответствию требованиям",
    "takedown_risk": "может снизить риск снятия или блокировки карточки по претензии",
    "fine_risk": "может снизить риск штрафных санкций",
    "block_risk": "может снизить риск блокировки карточки",
}

# advisory recommended_action text (mirrors the A4 allowlist)
_ACTION_TEXT = {
    "check_requirement": "Проверить, применимо ли это требование к данному товару.",
    "collect_document": "Собрать и проверить подтверждающие документы.",
    "verify_marketplace_terms": "Сверить условия с правилами маркетплейса.",
    "consult_lawyer": "При сомнениях обратиться к юристу или профильному специалисту.",
    "review_content_claim": "Проверить формулировки в карточке товара.",
}

# 5-part doctrine templates (what_happened / why_it_matters / meaning)
_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "product_certification": {
        "what_happened": "По товару не подтверждена сертификация/декларация.",
        "why_it_matters": "Для части категорий обязательны документы соответствия.",
        "meaning": "Возможно, требуется проверить, нужны ли документы для этой категории.",
    },
    "trademark_usage": {
        "what_happened": "Использование бренда/обозначения требует проверки прав.",
        "why_it_matters": "Использование чужого товарного знака может вызвать претензию.",
        "meaning": "Стоит проверить основания использования обозначения.",
    },
    "labeling_requirements": {
        "what_happened": "Не подтверждено соответствие требованиям маркировки/этикетки.",
        "why_it_matters": "Для ряда категорий маркировка обязательна.",
        "meaning": "Возможно, требуется проверить требования к маркировке.",
    },
    "marketplace_offer_terms": {
        "what_happened": "Не сверены условия оффера с правилами маркетплейса.",
        "why_it_matters": "Нарушение правил площадки может привести к санкциям.",
        "meaning": "Стоит сверить карточку с актуальными требованиями площадки.",
    },
    "return_policy_obligations": {
        "what_happened": "Не подтверждены обязательства по возврату.",
        "why_it_matters": "Правила возврата регулируются законом и площадкой.",
        "meaning": "Стоит проверить, соответствуют ли условия возврата требованиям.",
    },
    "content_claim_risk": {
        "what_happened": "В карточке есть формулировки, требующие проверки.",
        "why_it_matters": "Некоторые утверждения могут требовать обоснования.",
        "meaning": "Стоит проверить, подтверждены ли заявленные свойства.",
    },
}


def insight_key(snap: LegalSnapshot, requirement_type: str) -> str:
    ref = snap.sku or snap.subject_ref or "unknown"
    return f"legal_{requirement_type}:{snap.marketplace or 'unknown'}:{ref}"


def signal_fields(snap: LegalSnapshot, r) -> dict:
    """Deterministic advisory signal payload for a DETECTED requirement."""
    rt = r.requirement_type
    tpl = _TEMPLATES[rt]
    effect_kind = EFFECT_KIND[rt]
    return {
        "signal_key": f"legal_{rt}",
        "insight_key": insight_key(snap, rt),
        "requirement_type": rt,
        "category": CATEGORY[rt],
        "recommended_action_key": r.recommended_action,
        "alternative_action_keys": json.dumps(["consult_lawyer"]),
        # 5-part doctrine → DB columns what/why/meaning/what_to_do/expected_effect
        "what": tpl["what_happened"],
        "why": tpl["why_it_matters"],
        "meaning": tpl["meaning"],
        "what_to_do": _ACTION_TEXT.get(r.recommended_action, "Проверить требование."),
        "expected_effect": _EFFECT_TEXT[effect_kind],
        "priority_level": r.severity,
        "risk_level": r.risk_band,
        "effect_type": f"{effect_kind}_reduction",   # qualitative, never money
        "effect_band": r.risk_band,
        "confidence": None,                            # no numeric score
    }

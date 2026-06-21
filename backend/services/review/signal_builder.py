"""
Review Signal Builder (A5) — deterministic, template-only.

Turns a TRIGGERED RuleEvaluation into a seller-facing SignalDraft following the
PULT doctrine (what / why / meaning / what_to_do / expected_effect) + canonical
action + safety. No AI, no language-model use, no reply-text generation —
parametric templates filled from snapshot-derived evidence.

Safety mode = the policy default_mode (RISK → manual_only, ATTENTION/SAFE →
manual_approval). AUTO is NEVER set as the mode here — even when allowed_modes
permits it, that is only a possibility, not the recommended action.

No Fake Impact: expected_effect never promises rating/sales/guaranteed
improvement — only reputation-risk reduction / faster handling / quality
communication. `already_answered` is a STATUS, not a problem.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from .evaluation import RuleEvaluation


@dataclass(frozen=True)
class SignalDraft:
    problem_type: str
    signal_key: str
    insight_key: str
    recommended_action_key: str
    alternative_action_keys: Tuple[str, ...]
    what: str
    why: str
    meaning: str
    what_to_do: str
    expected_effect: str
    priority_level: str
    expected_effect_type: str
    effect_band: str
    confidence: float
    safety_category: Optional[str]
    safety_mode: Optional[str]


_ACTIONS: Mapping[str, Tuple[str, Tuple[str, ...]]] = {
    "unanswered_negative_review":  ("reply_manually", ("escalate",)),
    "unanswered_attention_review": ("reply_with_approval", ("dismiss",)),
    "safe_review_can_reply":       ("reply_with_approval", ("dismiss",)),
    "five_star_without_text":      ("reply_with_approval", ("dismiss",)),
    "complaint_detected":          ("escalate", ("reply_manually",)),
    "already_answered":            ("no_action", ()),
}

_EFFECT_BAND: Mapping[str, str] = {
    "unanswered_negative_review": "high", "unanswered_attention_review": "medium",
    "safe_review_can_reply": "low", "five_star_without_text": "low",
    "complaint_detected": "high", "already_answered": "low",
}

_TEMPLATES: Mapping[str, Mapping[str, str]] = {
    "unanswered_negative_review": {
        "what": "Негативный отзыв ({rating}★) без ответа.",
        "why": "Без ответа негатив виден другим покупателям и копит репутационный риск.",
        "meaning": "Молчание на негатив усиливает репутационный риск.",
        "what_to_do": "Ответить вручную — для негатива только ручной режим.",
        "expected_effect": "Может снизить репутационный риск.",
    },
    "unanswered_attention_review": {
        "what": "Спорный отзыв ({rating}★) без ответа.",
        "why": "Без ответа спорный отзыв остаётся без вашей позиции.",
        "meaning": "Отсутствие ответа оставляет вопрос открытым.",
        "what_to_do": "Подготовить ответ с вашим одобрением.",
        "expected_effect": "Может снизить репутационный риск.",
    },
    "safe_review_can_reply": {
        "what": "Положительный отзыв ({rating}★) без ответа.",
        "why": "Ответ поддерживает качество коммуникации с покупателями.",
        "meaning": "Положительный отзыв без ответа — упущенная коммуникация.",
        "what_to_do": "Поблагодарить с вашим одобрением.",
        "expected_effect": "Поддержание качества коммуникации.",
    },
    "five_star_without_text": {
        "what": "Отзыв 5★ без текста и без ответа.",
        "why": "Короткая благодарность поддерживает коммуникацию.",
        "meaning": "Простой положительный отзыв можно безопасно обработать.",
        "what_to_do": "Поблагодарить с вашим одобрением.",
        "expected_effect": "Поддержание качества коммуникации.",
    },
    "complaint_detected": {
        "what": "В отзыве признаки жалобы: {complaint_markers_found}.",
        "why": "Жалобы (брак/возврат/доставка) — высокий репутационный риск.",
        "meaning": "Жалоба требует ручного разбора, не шаблона.",
        "what_to_do": "Разобрать вручную или эскалировать.",
        "expected_effect": "Может снизить репутационный риск.",
    },
    "already_answered": {
        "what": "Отзыв уже обработан.",
        "why": "Ответ опубликован — действий не требуется.",
        "meaning": "Коммуникация по отзыву закрыта.",
        "what_to_do": "Действий не требуется.",
        "expected_effect": "Коммуникация поддержана.",
    },
}

_CONFIDENCE = {"reviews": 0.9, "requires_text": 0.7}


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fmt(text: str, evidence: Optional[Mapping[str, object]]) -> str:
    return text.format_map(_SafeDict(evidence or {}))


def build_signal(ev: RuleEvaluation, *, marketplace: Optional[str], sku: Optional[str],
                 review_id: Optional[str]) -> SignalDraft:
    """Deterministic SignalDraft for a TRIGGERED review rule. Template-only."""
    pt = ev.problem_type
    tpl = _TEMPLATES[pt]
    primary, alts = _ACTIONS[pt]
    e = ev.evidence or {}
    return SignalDraft(
        problem_type=pt,
        signal_key=f"rev_{pt}",
        # review_id is mandatory for dedup
        insight_key=f"rev_{pt}:{(marketplace or 'unknown')}:{(sku or 'unknown')}:{(review_id or 'unknown')}",
        recommended_action_key=primary,
        alternative_action_keys=tuple(alts),
        what=_fmt(tpl["what"], e),
        why=tpl["why"],
        meaning=tpl["meaning"],
        what_to_do=tpl["what_to_do"],
        expected_effect=tpl["expected_effect"],
        priority_level=ev.severity,
        expected_effect_type=ev.estimated_effect_type,
        effect_band=_EFFECT_BAND[pt],
        confidence=_CONFIDENCE.get(ev.detectability, 0.7),
        safety_category=e.get("safety_category"),
        # safety_mode = the policy default — never AUTO (default is never auto)
        safety_mode=e.get("default_mode"),
    )

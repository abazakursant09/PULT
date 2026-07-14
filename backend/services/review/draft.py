"""Deterministic, safe review-reply drafting.

Marketplace-neutral: takes only a rating, the review text, and the product name — no marketplace
knowledge. No model calls, no randomness, no fabrication: a draft is only ever produced for a real synced
review, and it makes no promise the seller cannot keep. It never admits a defect, offers a refund
or compensation, discusses price, or makes a legal statement. RISK reviews get no sendable draft
at all — a human writes those.

This is the opposite of the old random.choice generator that AR0 removed: the text is chosen
deterministically from the review's own facts (its rating band, whether it carries text, the
product name), so it reads as a specific reply rather than boilerplate, while staying safe.
"""
from __future__ import annotations

from typing import Optional

from .safety_policy import classify_safety, SAFE, ATTENTION, RISK

# What a draft must never contain, regardless of how it is produced. Kept here so a future generative
# path can reuse it as an outbound guardrail before any produced text reaches a seller.
FORBIDDEN_MARKERS = (
    "возврат", "верн", "компенсац", "скидк", "деньги", "рубл",
    "брак", "дефект", "гарантир", "суд", "юрист", "претензи", "бесплатн",
)


def _reason_for(decision) -> str:
    """Human-readable manual_required_reason for a non-SAFE review."""
    if decision.category == RISK:
        return "Негативный отзыв или жалоба — ответ пишется вручную."
    if decision.category == ATTENTION:
        return "Смешанный отзыв — проверьте черновик перед отправкой."
    return ""


def classify_review(rating: Optional[int], text: Optional[str]):
    """Reuse the existing deterministic safety policy. Returns (safety_category, reason).

    reason is empty for SAFE, a human-readable explanation for ATTENTION / RISK.
    """
    decision = classify_safety(rating, bool(text and text.strip()), text)
    return decision.category, _reason_for(decision)


def _greeting(name: Optional[str]) -> str:
    n = (name or "").strip()
    return f"Спасибо за ваш отзыв о товаре «{n}»!" if n else "Спасибо за ваш отзыв!"


def build_draft(rating: Optional[int], text: Optional[str], product_name: Optional[str]) -> Optional[str]:
    """Return a safe deterministic draft, or None when no sendable draft may be created.

    None for RISK (never auto-drafted for sending). For SAFE and ATTENTION a professional,
    promise-free reply is built from the review's own rating band and the product name. The
    result is asserted to contain no forbidden marker before it is returned — a deterministic
    outbound guardrail that also guards any future generative path.
    """
    category, _ = classify_review(rating, text)
    if category == RISK:
        return None

    greeting = _greeting(product_name)
    if rating is not None and rating >= 5:
        body = "Нам очень приятно, что товар вам понравился. Будем рады видеть вас снова!"
    elif rating == 4:
        body = "Рады, что товар в целом вам подошёл. Спасибо, что поделились мнением!"
    else:
        # ATTENTION with no clearly-positive rating (e.g. a 4-star with text): thank, invite
        # detail, promise nothing.
        body = ("Спасибо, что поделились впечатлениями. Нам важно ваше мнение, "
                "и мы обязательно его учтём.")

    draft = f"{greeting} {body}"

    # Deterministic outbound guardrail: a safe template must never carry a forbidden marker.
    low = draft.lower()
    assert not any(m in low for m in FORBIDDEN_MARKERS), "draft template contains a forbidden marker"
    return draft

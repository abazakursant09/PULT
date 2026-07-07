"""
Returns reason taxonomy (Returns-Reason Diagnosis) — deterministic, keyword-only.

Normalizes the buyer's OWN reported return reason (ImportedReturnRow.reason, free text,
"as reported") into a small, stable bucket. Pure deterministic substring matching — NO ML,
NO inference of an unstated cause. We categorize the reported text; we invent nothing.

Buckets:
  * five DIAGNOSED buckets (may produce a reason_share_rise signal),
  * two NON-DIAGNOSED buckets:
      - "unspecified" : reason is null/empty (never reported),
      - "other"       : reason is non-empty but matches no keyword.
    Both are COUNTED in the denominator (total returns) but NEVER produce a signal.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

UNSPECIFIED = "unspecified"
OTHER = "other"

_WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

# five diagnosed buckets, checked in this order (first keyword hit wins). Keywords are
# lowercased substrings matched against the lowercased reported reason.
_BUCKET_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("quality_defect",       ("брак", "дефект", "не работает", "сломан", "плохое качество")),
    ("size_fit",             ("размер", "мал", "велик", "не подошёл размер", "посадка")),
    ("mismatch_description", ("не соответствует", "отличается от описания", "не то",
                             "другой товар", "цвет")),
    ("damaged_delivery",     ("повреждён", "помят", "разбит", "повреждение при доставке")),
    ("changed_mind",         ("передумал", "не понравился", "отказ", "без причины")),
)

# the five diagnosed buckets, in declaration order (for stable iteration by callers)
DIAGNOSED_BUCKETS: Tuple[str, ...] = tuple(b for b, _ in _BUCKET_KEYWORDS)


def _matches(keyword: str, text: str, tokens: Tuple[str, ...]) -> bool:
    """A multi-word keyword (has a space) matches as a substring of the full text; a
    single-word keyword matches only as a TOKEN PREFIX — so 'мал' matches 'мал'/'маломерит'
    but NOT 'передумал' (where 'мал' is a mid-word substring)."""
    if " " in keyword:
        return keyword in text
    return any(tok.startswith(keyword) for tok in tokens)


def normalize_return_reason(reason: Optional[str]) -> str:
    """Map a reported return reason to a bucket. null/empty → 'unspecified';
    non-empty but unmatched → 'other'; otherwise the first matching diagnosed bucket."""
    if reason is None:
        return UNSPECIFIED
    text = reason.strip().lower()
    if not text:
        return UNSPECIFIED
    tokens = tuple(_WORD.findall(text))
    for bucket, keywords in _BUCKET_KEYWORDS:
        if any(_matches(kw, text, tokens) for kw in keywords):
            return bucket
    return OTHER

"""
Presentation Cards (Phase P0) — group FeedItems into seller-facing SKU cards.

A PresentationCard is ONE (marketplace, sku) grouping of the live FeedItems build_feed
produced for that product. P0 does grouping ONLY:
  * deterministic key = (marketplace, sku),
  * every FeedItem preserved, in the exact order build_feed returned it,
  * nothing hidden, nothing removed, no diagnosis field altered.

The card exposes read-only conveniences derived verbatim from its items (highest observed
severity, the distinct contributing contours, the verbatim recommendation + evidence lists)
plus a placeholder for the future root-cause narrative (Phase P3). No dedup, no
re-prioritization, no synthesis here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from services.decision_feed.builder import FeedItem

# observed severity rank (lower = more severe). Mirrors the Decision Feed's Priority Engine
# severity class; used ONLY to surface a card's highest observed severity — no re-ordering,
# no numeric score, no forecast.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}


@dataclass(frozen=True)
class PresentationCard:
    """One seller-facing card = all live FeedItems for one (marketplace, sku). Read-only view."""
    marketplace: Optional[str]
    sku: Optional[str]
    # the grouped FeedItems, in the SAME order build_feed returned them (never re-sorted here)
    items: List[FeedItem]
    # highest OBSERVED severity across the grouped items (critical|high|medium|low|None) — the
    # most severe member's priority class, verbatim. Not a score, not computed from money.
    highest_severity: Optional[str]
    # distinct contours present, in first-appearance order (e.g. ("returns", "seo"))
    contributing_contours: Tuple[str, ...]
    # verbatim recommendation lines (recommended_action of each item that has one), order
    # preserved. P0 keeps duplicates — dedup is Phase P1.
    recommendations: List[str]
    # verbatim per-item evidence (contour + doctrine what/why), order preserved
    evidence: List[dict]
    # Phase P3 placeholder — a synthesized cross-diagnosis explanation. Always None in P0.
    root_cause_narrative: Optional[str] = None
    # stable grouping key, "<marketplace>:<sku>" (or the raw None-safe pair repr)
    group_key: str = field(default="")


def _group_key(marketplace: Optional[str], sku: Optional[str]) -> str:
    return f"{marketplace}:{sku}"


def _highest_severity(items: List[FeedItem]) -> Optional[str]:
    """The most severe observed priority class among the items (verbatim, no computation)."""
    best = None
    best_rank = _SEVERITY_RANK[None]
    for it in items:
        rank = _SEVERITY_RANK.get(it._priority_bucket, _SEVERITY_RANK[None])
        if rank < best_rank:
            best_rank, best = rank, it._priority_bucket
    return best


def _card(marketplace: Optional[str], sku: Optional[str], items: List[FeedItem]) -> PresentationCard:
    contours: List[str] = []
    recommendations: List[str] = []
    evidence: List[dict] = []
    for it in items:
        if it.contour not in contours:
            contours.append(it.contour)
        if it.recommended_action:
            recommendations.append(it.recommended_action)     # verbatim, duplicates kept (P0)
        evidence.append({
            "contour": it.contour,
            "item_key": it.item_key,
            "title": it.title,
            "what_happened": it.what_happened,
            "why_it_matters": it.why_it_matters,
        })
    return PresentationCard(
        marketplace=marketplace, sku=sku, items=list(items),
        highest_severity=_highest_severity(items),
        contributing_contours=tuple(contours),
        recommendations=recommendations, evidence=evidence,
        root_cause_narrative=None,
        group_key=_group_key(marketplace, sku),
    )


def build_presentation_cards(items: List[FeedItem]) -> List[PresentationCard]:
    """Group FeedItems into (marketplace, sku) PresentationCards. Deterministic: cards appear
    in first-appearance order of their group; items inside a card keep build_feed's order.
    Read-only — reads FeedItems ONLY, changes no diagnosis, writes nothing. Empty in → empty out.

    NOTE: items with a None marketplace and/or None sku form their own group under that exact
    (None-safe) key — they are never dropped (nothing hidden)."""
    grouped: "dict[Tuple[Optional[str], Optional[str]], List[FeedItem]]" = {}
    order: List[Tuple[Optional[str], Optional[str]]] = []
    for it in items:
        key = (it.marketplace, it.sku)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(it)
    return [_card(mp, sku, grouped[(mp, sku)]) for (mp, sku) in order]

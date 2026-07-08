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


def _normalize_recommendation(text: str) -> str:
    """Deterministic merge key for a recommendation: collapse internal whitespace + casefold.
    Two items whose recommendation normalizes to the same key are the SAME seller action. No
    fabrication — the key is derived from the verbatim recommended_action only."""
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class RecommendationGroup:
    """One deduped seller recommendation (Phase P1) — every FeedItem whose recommendation
    normalizes to the same key, grouped under a single seller-facing line. Full traceability:
    the contributing items / contours / problem_types are all preserved. No diagnosis merged,
    no evidence removed — only the recommendation TEXT is deduplicated."""
    recommendation: str                       # seller-facing text (first occurrence, verbatim)
    items: List[FeedItem]                     # every FeedItem contributing this recommendation
    contributing_contours: Tuple[str, ...]    # distinct contours, first-appearance order
    contributing_problem_types: Tuple[str, ...]  # distinct problem_types, first-appearance order
    # Phase P2 — highest OBSERVED severity among this group's items (critical|high|medium|low|
    # None), read verbatim from the members' priority class. Not a score, not computed from money.
    # Used ONLY to order the recommendation_groups list; item order inside the group is untouched.
    group_severity: Optional[str] = None
    # Phase P3 — deterministic read-layer narrative: when ≥2 diagnoses converge on this one
    # seller action, state that observed convergence verbatim (no invented causality, no money,
    # no forecast). None when the group has fewer than 2 items (not enough evidence).
    root_cause_narrative: Optional[str] = None


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
    # Phase P1 — deduped seller recommendations: identical (normalized) recommendation TEXT
    # merged into one group, each carrying its contributing items/contours/problem_types.
    # Default empty so P0 callers/serialization are unaffected.
    recommendation_groups: List[RecommendationGroup] = field(default_factory=list)
    # Phase P3 — deterministic mirror of the first (most-severe, P2-ordered) RecommendationGroup
    # that carries a root_cause_narrative; None when no group qualifies. Read-layer only, no
    # synthesis — the card points at the strongest observed convergence, inventing nothing.
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


def _problem_type(it: FeedItem) -> Optional[str]:
    """The item's problem_type, read verbatim from the FeedItem's source_context (never
    recomputed). None for items that carry no problem_type (e.g. decision_outcome)."""
    return it.source_context.get("problem_type") if it.source_context else None


def _root_cause_narrative(group_items: List[FeedItem], contours: List[str],
                          problem_types: List[str], recommendation: str) -> Optional[str]:
    """Phase P3 — deterministic root-cause line for ONE RecommendationGroup. Returns None unless
    ≥2 FeedItems converge on this action (not enough evidence → None). The text only STATES the
    observed convergence — the diagnoses literally share this normalized action — and invents no
    causality, no money, no forecast. contours / problem_types are sorted-unique for stable text."""
    if len(group_items) < 2:
        return None
    n = len(group_items)
    contours_txt = ", ".join(sorted(set(contours)))
    ptypes_txt = ", ".join(sorted(set(problem_types))) if problem_types else "—"
    return (f"{n} диагнозов сходятся на одном действии «{recommendation}» — "
            f"контуры: {contours_txt}; типы проблем: {ptypes_txt}. "
            f"Общая причина — единое действие устраняет их все.")


def _recommendation_groups(items: List[FeedItem]) -> List[RecommendationGroup]:
    """Phase P1 — dedup identical (normalized) recommendation TEXT into one group per key.
    Items keep build_feed order inside a group. Items with no recommended_action join no group
    (they stay fully present in card.items / evidence). Every item WITH a recommendation belongs
    to exactly one group; no diagnosis merged, no recommendation fabricated.

    Phase P2 — the returned groups are ordered by highest OBSERVED severity (most severe first);
    equal-severity groups keep stable first-appearance order; None-severity groups sort last.
    Ordering acts on the groups list ONLY — item order inside a group, and card.items /
    recommendations / evidence order, are all untouched."""
    order: List[str] = []
    by_key: "dict[str, List[FeedItem]]" = {}
    display: "dict[str, str]" = {}
    for it in items:
        rec = it.recommended_action
        if not rec:
            continue                                    # no recommendation → no group
        key = _normalize_recommendation(rec)
        if key not in by_key:
            by_key[key] = []
            display[key] = rec                          # first occurrence = seller-facing text
            order.append(key)
        by_key[key].append(it)

    groups: List[RecommendationGroup] = []
    for key in order:
        grp_items = by_key[key]
        contours: List[str] = []
        ptypes: List[str] = []
        for it in grp_items:
            if it.contour not in contours:
                contours.append(it.contour)
            pt = _problem_type(it)
            if pt is not None and pt not in ptypes:
                ptypes.append(pt)
        groups.append(RecommendationGroup(
            recommendation=display[key], items=list(grp_items),
            contributing_contours=tuple(contours),
            contributing_problem_types=tuple(ptypes),
            group_severity=_highest_severity(grp_items),
            root_cause_narrative=_root_cause_narrative(
                grp_items, contours, ptypes, display[key])))
    # Phase P2 — order groups most-severe first. Python's sort is stable, so equal-severity
    # groups keep their first-appearance order; None maps to the last rank (sorts last).
    groups.sort(key=lambda g: _SEVERITY_RANK.get(g.group_severity, _SEVERITY_RANK[None]))
    return groups


def _card(marketplace: Optional[str], sku: Optional[str], items: List[FeedItem]) -> PresentationCard:
    contours: List[str] = []
    recommendations: List[str] = []
    evidence: List[dict] = []
    for it in items:
        if it.contour not in contours:
            contours.append(it.contour)
        if it.recommended_action:
            recommendations.append(it.recommended_action)     # verbatim, duplicates kept (raw)
        evidence.append({
            "contour": it.contour,
            "item_key": it.item_key,
            "title": it.title,
            "what_happened": it.what_happened,
            "why_it_matters": it.why_it_matters,
        })
    groups = _recommendation_groups(items)
    # Phase P3 — deterministic mirror: surface the first (most-severe, P2-ordered) group that
    # carries a narrative. Not new synthesis — the card just points to the strongest convergence.
    card_narrative = next((g.root_cause_narrative for g in groups if g.root_cause_narrative), None)
    return PresentationCard(
        marketplace=marketplace, sku=sku, items=list(items),
        highest_severity=_highest_severity(items),
        contributing_contours=tuple(contours),
        recommendations=recommendations, evidence=evidence,
        recommendation_groups=groups,
        root_cause_narrative=card_narrative,
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

"""
Presentation Intelligence P1 — recommendation deduplication.

Inside each PresentationCard, identical (normalized) recommendation TEXT is merged into
one RecommendationGroup, while every diagnosis / FeedItem / evidence is preserved and each
group stays traceable back to its contributing items, contours and problem_types.

P1 merges RECOMMENDATIONS only — it does NOT merge diagnoses, remove evidence, change
priority, build root cause, or change P0 grouping.
"""
import uuid

from services.decision_feed.builder import FeedItem
from services.presentation.cards import (
    build_presentation_cards, RecommendationGroup, _normalize_recommendation)


def _fi(contour, sku, marketplace, *, rec="сделать X", problem_type=None,
        item_key=None, severity="high"):
    it = FeedItem(
        item_key=item_key or f"{contour}_x:{marketplace}:{sku}",
        contour=contour, source_table=f"{contour}_signal", source_id=str(uuid.uuid4()),
        source_status="active", attention_state="new", marketplace=marketplace, sku=sku,
        title=f"{contour} title", what_happened="что-то", why_it_matters="почему",
        meaning="смысл", recommended_action=rec, expected_effect="эффект")
    it._priority_bucket = severity
    if problem_type is not None:
        it.source_context = {"problem_type": problem_type}
    return it


def _card_for(items):
    cards = build_presentation_cards(items)
    assert len(cards) == 1
    return cards[0]


# ── 1. identical recommendations merge into one group ────────────────────────

def test_identical_recommendations_merge():
    items = [_fi("seo", "SKU1", "wb", rec="improve_listing", problem_type="title_too_short"),
             _fi("advertising", "SKU1", "wb", rec="improve_listing", problem_type="ad_on_bad_listing")]
    c = _card_for(items)
    assert len(c.recommendation_groups) == 1
    g = c.recommendation_groups[0]
    assert isinstance(g, RecommendationGroup)
    assert g.recommendation == "improve_listing"
    assert len(g.items) == 2
    assert set(g.contributing_contours) == {"seo", "advertising"}
    assert set(g.contributing_problem_types) == {"title_too_short", "ad_on_bad_listing"}


# ── 2. different recommendations stay separate ───────────────────────────────

def test_different_recommendations_separate():
    items = [_fi("seo", "SKU1", "wb", rec="improve_listing"),
             _fi("returns", "SKU1", "wb", rec="проверить причины возвратов")]
    c = _card_for(items)
    assert len(c.recommendation_groups) == 2
    assert [g.recommendation for g in c.recommendation_groups] == \
        ["improve_listing", "проверить причины возвратов"]


# ── 3. normalization: whitespace + case merge; genuinely different stay apart ─

def test_normalization_merges_whitespace_and_case():
    items = [_fi("seo", "SKU1", "wb", rec="Improve  Listing"),
             _fi("advertising", "SKU1", "wb", rec="improve listing")]
    c = _card_for(items)
    assert len(c.recommendation_groups) == 1                 # merged despite spacing/case
    assert _normalize_recommendation("Improve  Listing") == _normalize_recommendation("improve listing")


# ── 4. one FeedItem belongs to exactly one recommendation group ──────────────

def test_item_in_exactly_one_group():
    items = [_fi("seo", "SKU1", "wb", rec="A", item_key="i1"),
             _fi("advertising", "SKU1", "wb", rec="A", item_key="i2"),
             _fi("returns", "SKU1", "wb", rec="B", item_key="i3")]
    c = _card_for(items)
    seen = {}
    for gi, g in enumerate(c.recommendation_groups):
        for it in g.items:
            assert it.item_key not in seen, "item appeared in two groups"
            seen[it.item_key] = gi
    assert set(seen) == {"i1", "i2", "i3"}                    # every rec-bearing item grouped once


# ── 5. every diagnosis remains reachable (nothing merged away) ───────────────

def test_every_diagnosis_reachable():
    items = [_fi("seo", "SKU1", "wb", rec="A"), _fi("advertising", "SKU1", "wb", rec="A"),
             _fi("rating", "SKU1", "wb", rec="B")]
    c = _card_for(items)
    # all FeedItems still present on the card (P0 grouping untouched)
    assert len(c.items) == 3
    # union of grouped items == all rec-bearing items
    grouped = [it for g in c.recommendation_groups for it in g.items]
    assert {id(it) for it in grouped} == {id(it) for it in items}
    # evidence still one entry per item
    assert len(c.evidence) == 3


# ── 6. item with no recommendation joins no group but stays on the card ──────

def test_item_without_recommendation_still_present():
    items = [_fi("seo", "SKU1", "wb", rec="A"),
             _fi("decision_outcome", "SKU1", "wb", rec=None)]
    c = _card_for(items)
    assert len(c.recommendation_groups) == 1                 # only the rec-bearing item grouped
    assert len(c.items) == 2                                 # both still on the card
    assert len(c.evidence) == 2


# ── 7. deterministic grouping ────────────────────────────────────────────────

def test_deterministic_grouping():
    items = [_fi("seo", "SKU1", "wb", rec="A"), _fi("returns", "SKU1", "wb", rec="B"),
             _fi("advertising", "SKU1", "wb", rec="A")]
    a = build_presentation_cards(items)[0]
    b = build_presentation_cards(items)[0]
    assert [g.recommendation for g in a.recommendation_groups] == \
           [g.recommendation for g in b.recommendation_groups]
    assert [[it.item_key for it in g.items] for g in a.recommendation_groups] == \
           [[it.item_key for it in g.items] for g in b.recommendation_groups]


# ── 8. ordering preserved (first-appearance of key; item order inside) ───────

def test_ordering_preserved():
    items = [_fi("seo", "SKU1", "wb", rec="A", item_key="a1"),
             _fi("returns", "SKU1", "wb", rec="B", item_key="b1"),
             _fi("advertising", "SKU1", "wb", rec="A", item_key="a2")]
    c = _card_for(items)
    # group order = first-appearance of normalized key: A before B
    assert [g.recommendation for g in c.recommendation_groups] == ["A", "B"]
    # inside group A, items in build_feed order
    assert [it.item_key for it in c.recommendation_groups[0].items] == ["a1", "a2"]


# ── 9. duplicate recommendations disappear from the seller (deduped) view ────

def test_duplicates_gone_from_seller_view():
    items = [_fi("seo", "SKU1", "wb", rec="improve_listing"),
             _fi("advertising", "SKU1", "wb", rec="improve_listing"),
             _fi("growth", "SKU1", "wb", rec="improve_listing")]
    c = _card_for(items)
    # raw P0 list still has all three (verbatim), but the deduped view collapses to one
    assert c.recommendations == ["improve_listing", "improve_listing", "improve_listing"]
    assert [g.recommendation for g in c.recommendation_groups] == ["improve_listing"]
    assert len(c.recommendation_groups[0].items) == 3


# ── 10. evidence preserved verbatim (no diagnosis changed) ───────────────────

def test_evidence_preserved():
    src = _fi("returns", "SKU1", "wb", rec="проверить", problem_type="return_rate_rise")
    c = _card_for([src])
    assert c.evidence[0]["contour"] == "returns"
    assert c.items[0] is src                                  # same object
    g = c.recommendation_groups[0]
    assert g.items[0] is src and g.contributing_problem_types == ("return_rate_rise",)


# ── 11. dedup is per-card, not across cards ──────────────────────────────────

def test_dedup_is_per_card():
    items = [_fi("seo", "SKU1", "wb", rec="improve_listing"),
             _fi("seo", "SKU2", "wb", rec="improve_listing")]
    cards = build_presentation_cards(items)
    assert len(cards) == 2
    for c in cards:
        assert len(c.recommendation_groups) == 1
        assert len(c.recommendation_groups[0].items) == 1    # not merged across SKUs

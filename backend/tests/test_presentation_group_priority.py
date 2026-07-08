"""
Presentation Intelligence P2 — recommendation group priority / ordering.

Inside each PresentationCard the recommendation_groups list is ordered by the highest
OBSERVED severity among each group's contributing FeedItems: most severe first, equal
severity keeps stable first-appearance order, None-severity sorts last. group_severity is
read verbatim from the members' priority class — no new score, no money math, no forecast.

P2 orders recommendation_groups ONLY. It does NOT re-order card.items / card.recommendations
/ card.evidence, does NOT change P0 (marketplace, sku) grouping, does NOT change diagnosis,
and adds NO root-cause narrative.
"""
import uuid

from services.decision_feed.builder import FeedItem
from services.presentation.cards import build_presentation_cards, RecommendationGroup


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


# ── 1. groups ordered most-severe first ──────────────────────────────────────

def test_groups_ordered_most_severe_first():
    # build_feed / first-appearance order is low, critical, medium — P2 must re-order groups
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="medium")]
    c = _card_for(items)
    assert [g.recommendation for g in c.recommendation_groups] == ["B", "C", "A"]
    assert [g.group_severity for g in c.recommendation_groups] == ["critical", "medium", "low"]


# ── 2. group_severity = highest observed severity of member items ────────────

def test_group_severity_is_highest_of_members():
    # same recommendation "A" from three items of differing severity → one group, max = critical
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="A", severity="critical"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="medium")]
    c = _card_for(items)
    assert len(c.recommendation_groups) == 1
    assert c.recommendation_groups[0].group_severity == "critical"


# ── 3. equal-severity groups keep stable first-appearance order ──────────────

def test_equal_severity_keeps_first_appearance():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="high"),
             _fi("returns", "SKU1", "wb", rec="B", severity="high"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="high")]
    c = _card_for(items)
    assert [g.recommendation for g in c.recommendation_groups] == ["A", "B", "C"]


# ── 4. None-severity groups sort last ────────────────────────────────────────

def test_none_severity_sorts_last():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity=None),
             _fi("returns", "SKU1", "wb", rec="B", severity="medium"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="high")]
    c = _card_for(items)
    assert [g.recommendation for g in c.recommendation_groups] == ["C", "B", "A"]
    assert c.recommendation_groups[-1].group_severity is None


# ── 5. card.items order unchanged (build_feed order) ─────────────────────────

def test_card_items_order_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low", item_key="i1"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical", item_key="i2"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="medium", item_key="i3")]
    c = _card_for(items)
    assert [it.item_key for it in c.items] == ["i1", "i2", "i3"]     # untouched by group sort


# ── 6. card.recommendations order unchanged (verbatim P0 list) ───────────────

def test_card_recommendations_order_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="medium")]
    c = _card_for(items)
    assert c.recommendations == ["A", "B", "C"]                     # raw first-appearance kept


# ── 7. card.evidence order unchanged ─────────────────────────────────────────

def test_card_evidence_order_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="medium")]
    c = _card_for(items)
    assert [e["contour"] for e in c.evidence] == ["seo", "returns", "advertising"]


# ── 8. every diagnosis remains reachable; no evidence lost ───────────────────

def test_every_diagnosis_reachable_no_evidence_lost():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical"),
             _fi("advertising", "SKU1", "wb", rec="A", severity="medium")]
    c = _card_for(items)
    grouped = [it for g in c.recommendation_groups for it in g.items]
    assert {id(it) for it in grouped} == {id(it) for it in items}   # all items still reachable
    assert len(c.items) == 3 and len(c.evidence) == 3               # nothing lost


# ── 9. traceability from group back to contributing items preserved ──────────

def test_traceability_preserved():
    a1 = _fi("seo", "SKU1", "wb", rec="A", severity="low", problem_type="p_seo", item_key="a1")
    a2 = _fi("returns", "SKU1", "wb", rec="A", severity="critical", problem_type="p_ret", item_key="a2")
    c = _card_for([a1, a2])
    g = c.recommendation_groups[0]
    assert [it.item_key for it in g.items] == ["a1", "a2"]          # item order inside group kept
    assert set(g.contributing_contours) == {"seo", "returns"}
    assert set(g.contributing_problem_types) == {"p_seo", "p_ret"}
    assert g.group_severity == "critical"


# ── 10. grouping by marketplace+sku unchanged (ordering is per-card) ─────────

def test_marketplace_sku_grouping_unchanged():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="low"),
             _fi("returns", "SKU2", "wb", rec="B", severity="critical")]
    cards = build_presentation_cards(items)
    assert len(cards) == 2
    assert {(c.marketplace, c.sku) for c in cards} == {("wb", "SKU1"), ("wb", "SKU2")}
    for c in cards:
        assert len(c.recommendation_groups) == 1                   # no cross-card merge/reorder


# ── 11. deterministic output ─────────────────────────────────────────────────

def test_deterministic_output():
    items = [_fi("seo", "SKU1", "wb", rec="A", severity="medium"),
             _fi("returns", "SKU1", "wb", rec="B", severity="critical"),
             _fi("advertising", "SKU1", "wb", rec="C", severity="medium")]
    a = build_presentation_cards(items)[0]
    b = build_presentation_cards(items)[0]
    assert [(g.recommendation, g.group_severity) for g in a.recommendation_groups] == \
           [(g.recommendation, g.group_severity) for g in b.recommendation_groups]
    # medium-tie (A before C) resolved by first-appearance, critical (B) first
    assert [g.recommendation for g in a.recommendation_groups] == ["B", "A", "C"]


# ── 12. empty feed still returns empty list ──────────────────────────────────

def test_empty_feed_returns_empty():
    assert build_presentation_cards([]) == []


# ── 13. RecommendationGroup carries group_severity field ─────────────────────

def test_group_carries_severity_field():
    c = _card_for([_fi("seo", "SKU1", "wb", rec="A", severity="high")])
    g = c.recommendation_groups[0]
    assert isinstance(g, RecommendationGroup)
    assert g.group_severity == "high"

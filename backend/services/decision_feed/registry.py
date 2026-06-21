"""
Feed Sources registry (Daily Decision Feed A2) — the contract that maps each
contour into the future feed, WITHOUT computing anything.

For every contour it declares:
  * contour
  * signal_table     — where the live items are read from (NEVER copied here)
  * item_key_rule    — how the canonical, stable feed item_key is formed. Reuses the
                       Decision Outcome canonical policy: the canonical 3-part
                       insight_key for the five engines (Review uses its canonical
                       3-part key, review_id NOT part of the key), and decision_id
                       for the Decision Outcome contour. No new key format.
  * lifecycle_source — the column/state the feed reads to know an item is live
  * priority_source  — the existing field a later sprint MAY order by. Declared
                       only — A2 computes NO priority, NO score, NO ranking.

Pure declaration. No DB, no key building, no aggregation, no ranking. This is the
contract A3 (aggregation) will read; A2 only fixes the source set and proves it
covers all six contours.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# canonical item_key rules (reuse Decision Outcome canonical policy)
ITEM_KEY_CANONICAL_INSIGHT = "canonical_insight_key"   # 5 engines: <prefix>_<type>:<mp>:<sku>
ITEM_KEY_DECISION_ID = "decision_id"                   # Decision Outcome: decisions.id


@dataclass(frozen=True)
class FeedSource:
    contour: str
    signal_table: str
    item_key_rule: str
    lifecycle_source: str      # field/state read to decide an item is live
    priority_source: str       # existing field a later sprint MAY order by (NOT computed in A2)


FEED_SOURCES: Tuple[FeedSource, ...] = (
    FeedSource("seo", "seo_signal", ITEM_KEY_CANONICAL_INSIGHT, "status", "priority_level"),
    FeedSource("advertising", "advertising_signal", ITEM_KEY_CANONICAL_INSIGHT, "status", "priority_level"),
    # Review's stored insight_key is 4-part; the feed uses its canonical 3-part key
    # (review_id is NOT part of the item_key) — see Decision Outcome A3 normalization.
    FeedSource("review", "review_signal", ITEM_KEY_CANONICAL_INSIGHT, "status", "priority_level"),
    FeedSource("growth", "growth_signal", ITEM_KEY_CANONICAL_INSIGHT, "status", "priority_level"),
    FeedSource("legal", "legal_signal", ITEM_KEY_CANONICAL_INSIGHT, "status", "priority_level"),
    # Decision Outcome surfaces proven effects, keyed by decision_id.
    FeedSource("decision_outcome", "engine_effect_observation", ITEM_KEY_DECISION_ID,
               "effect_band", "effect_band"),
)

BY_CONTOUR = {s.contour: s for s in FEED_SOURCES}
CONTOURS = tuple(s.contour for s in FEED_SOURCES)

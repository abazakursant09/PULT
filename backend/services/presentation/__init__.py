"""
Presentation Intelligence (read-layer, synthesis only).

Sits ABOVE the Decision Feed: consumes the FeedItems that build_feed already aggregated
and re-frames them for the seller. It reads FeedItems ONLY — never a producer, never a
signal table, never the marketplace. It changes no diagnosis and writes nothing.

    Producer → Signal → Decision Feed (aggregation) → Presentation Intelligence (synthesis)
        → Presentation Cards → Today / Dashboard / Telegram / Copilot

Phase P0 = the foundation: the PresentationCard DTO + a builder that GROUPS FeedItems by
(marketplace, sku). No dedup, no re-prioritization, no root-cause synthesis, no volume
management — those are later phases. Nothing is hidden or removed; a card is a view over
the exact FeedItems build_feed returned, order preserved.
"""
from .cards import PresentationCard, RecommendationGroup, build_presentation_cards

__all__ = ["PresentationCard", "RecommendationGroup", "build_presentation_cards"]

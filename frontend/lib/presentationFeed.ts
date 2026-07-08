// Presentation-feed client logic (frontend-only, pure — no I/O).
//
// The Dashboard consumes GET /api/presentation/cards: PresentationCard[] where each card
// nests the SAME DecisionFeedItem objects (by item_key) inside recommendation_groups and in
// card.items. Attention mutations still run through api.decisionFeed on item_key; these pure
// helpers keep the nested card state consistent after each mutation, with NO ghost items and
// NO empty groups/cards. No backend call, no fabricated data — items are only patched/removed.

import type { DecisionFeedItem, PresentationCard, RecommendationGroup } from './api'

// Items present on the card but in NO recommendation group (backend groups only items that
// carry a recommended_action; e.g. decision_outcome items have none). They must still render —
// never silently dropped — so the panel shows them as bare cards after the groups.
export function ungroupedItems(card: PresentationCard): DecisionFeedItem[] {
  const grouped = new Set<string>()
  for (const g of card.recommendation_groups) for (const it of g.items) grouped.add(it.item_key)
  return card.items.filter((it) => !grouped.has(it.item_key))
}

// seen / acted: patch attention_state in place everywhere the item appears (card.items AND
// each group's items). Card/group membership is unchanged — the item stays visible.
export function patchItemState(
  cards: PresentationCard[], itemKey: string, state: string,
): PresentationCard[] {
  const patch = (it: DecisionFeedItem): DecisionFeedItem =>
    it.item_key === itemKey ? { ...it, attention_state: state } : it
  return cards.map((c) => ({
    ...c,
    items: c.items.map(patch),
    recommendation_groups: c.recommendation_groups.map((g) => ({ ...g, items: g.items.map(patch) })),
  }))
}

// snooze / dismiss: remove the item from card.items and from every group; drop a group that
// becomes empty; drop a card whose items are all gone. Returns a new array (no mutation).
export function removeItem(cards: PresentationCard[], itemKey: string): PresentationCard[] {
  const out: PresentationCard[] = []
  for (const c of cards) {
    const items = c.items.filter((it) => it.item_key !== itemKey)
    const groups: RecommendationGroup[] = c.recommendation_groups
      .map((g) => ({ ...g, items: g.items.filter((it) => it.item_key !== itemKey) }))
      .filter((g) => g.items.length > 0)                 // no empty groups
    if (items.length === 0) continue                     // no empty cards
    out.push({ ...c, items, recommendation_groups: groups })
  }
  return out
}

// The globally-first feed item, in build_feed order (cards keep first-appearance order; each
// card.items keeps build_feed order). Used by skipTopAction to identify TodayFocus's top_action.
export function firstItemKey(cards: PresentationCard[]): string | null {
  for (const c of cards) {
    if (c.items.length > 0) return c.items[0].item_key
    for (const g of c.recommendation_groups) if (g.items.length > 0) return g.items[0].item_key
  }
  return null
}

// De-dupe with TodayFocus: drop the feed's first live item (== build_feed[0] == top_action).
// Only meaningful in the UNFILTERED view; the caller gates on contour === null.
export function skipTopAction(cards: PresentationCard[]): PresentationCard[] {
  const key = firstItemKey(cards)
  return key ? removeItem(cards, key) : cards
}

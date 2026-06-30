// Product grouping for the Decision Feed (frontend-only).
//
// Re-buckets canonical feed items by PRODUCT — normalize(marketplace) + sku — without
// touching backend, build_feed, FeedItem shape, or group_key. group_key keeps its
// existing meaning ("alternatives of one problem"); product grouping is a separate,
// outer axis: Product → Problems → Alternatives. Pure, no I/O, no fabricated data.

import type { DecisionFeedItem } from './api'

// canonical marketplace key — collapses the raw variants the backend stores
// ("wb"/"wildberries", "yandex"/"yandex_market") so one product is one group.
const _MP_CANON: Record<string, string> = {
  wb: 'wb', wildberries: 'wb',
  ozon: 'ozon',
  yandex: 'yandex', yandex_market: 'yandex',
  megamarket: 'megamarket',
}

export function normalizeMarketplace(mp: string | null | undefined): string {
  if (!mp) return ''
  const k = mp.toLowerCase()
  return _MP_CANON[k] ?? k
}

const _MP_LABEL: Record<string, string> = {
  wb: 'Wildberries', ozon: 'Ozon', yandex: 'Яндекс Маркет', megamarket: 'Megamarket',
}

export function marketplaceLabel(mp: string | null | undefined): string {
  const c = normalizeMarketplace(mp)
  return _MP_LABEL[c] ?? (mp ?? '')
}

// Russian plural for "рекомендация" — count is real (items in the group), never invented.
export function recommendationsLabel(n: number): string {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return `${n} рекомендация`
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return `${n} рекомендации`
  return `${n} рекомендаций`
}

export interface ProductGroup {
  key: string
  sku: string | null
  marketplace: string | null
  items: DecisionFeedItem[]
}

// Group items by product, preserving build_feed order (first appearance wins). Items
// without an sku fall back to their own solo group keyed by item_key (never merged).
export function groupByProduct(items: DecisionFeedItem[]): ProductGroup[] {
  const order: string[] = []
  const map = new Map<string, ProductGroup>()
  for (const it of items) {
    const key = it.sku
      ? `${normalizeMarketplace(it.marketplace)}:${it.sku}`
      : `solo:${it.item_key}`
    let g = map.get(key)
    if (!g) {
      g = { key, sku: it.sku, marketplace: it.marketplace, items: [] }
      map.set(key, g)
      order.push(key)
    }
    g.items.push(it)
  }
  return order.map((k) => map.get(k)!)
}

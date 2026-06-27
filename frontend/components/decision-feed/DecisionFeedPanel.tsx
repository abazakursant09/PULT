'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { DecisionFeedItem } from '@/lib/api'
import { DecisionFeedFilters } from './DecisionFeedFilters'
import { DecisionFeedCard } from './DecisionFeedCard'
import { DecisionFeedEmptyState } from './DecisionFeedEmptyState'

// "Что требует внимания сегодня" — главный видимый слой PULT. Список решений из
// всех контуров, не отчёт и не BI. Без рейтинга, без numeric priority, без прогноза.

type Action = 'seen' | 'snooze' | 'dismiss' | 'act'

export function DecisionFeedPanel() {
  const [contour, setContour] = useState<string | null>(null)
  const [items, setItems] = useState<DecisionFeedItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const resp = await api.decisionFeed.getFeed({ contour: contour ?? undefined, limit: 50 })
        if (alive) setItems(resp.items)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [contour])

  // Group items by group_key (canonical insight_key, carries marketplace → WB/Ozon
  // never merge). Items without a group_key stay standalone (keyed by item_key).
  // Order is preserved by first appearance.
  function groupItems(list: DecisionFeedItem[]): DecisionFeedItem[][] {
    const order: string[] = []
    const map = new Map<string, DecisionFeedItem[]>()
    for (const it of list) {
      const key = it.group_key ?? `solo:${it.item_key}`
      if (!map.has(key)) { map.set(key, []); order.push(key) }
      map.get(key)!.push(it)
    }
    // primary first within each group
    return order.map((k) => {
      const arr = map.get(k)!
      return [...arr].sort(
        (a: DecisionFeedItem, b: DecisionFeedItem) =>
          (a.action_role === 'primary' ? -1 : 0) - (b.action_role === 'primary' ? -1 : 0),
      )
    })
  }

  function roleLabel(it: DecisionFeedItem): string | null {
    if (it.action_role === 'primary') return 'Основной вариант'
    if (it.action_role === 'alternative') return 'Альтернатива'
    return null
  }

  // local update so the list reacts immediately after an action
  function onChanged(itemKey: string, action: Action) {
    setItems((prev) => {
      if (action === 'snooze' || action === 'dismiss') {
        return prev.filter((i) => i.item_key !== itemKey)   // hidden by default
      }
      const state = action === 'seen' ? 'seen' : 'acted'
      return prev.map((i) => (i.item_key === itemKey ? { ...i, attention_state: state } : i))
    })
  }

  return (
    <div className="s-card" style={{ marginBottom: 18 }}>
      <div style={{ marginBottom: 4 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
          Что требует внимания сегодня
        </h2>
        <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
          Собрано из SEO, рекламы, отзывов, роста, юридических рисков и доказанных эффектов решений.
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <DecisionFeedFilters value={contour} onChange={setContour} />

        {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка…</div>}
        {error && <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить: {error}</div>}

        {!loading && !error && (
          items.length === 0 ? (
            <DecisionFeedEmptyState />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {groupItems(items).map((group) => (
                group.length === 1 ? (
                  <DecisionFeedCard key={group[0].item_key} item={group[0]} onChanged={onChanged} />
                ) : (
                  <div key={group[0].group_key ?? group[0].item_key} style={{
                    border: '1px solid var(--line)', borderRadius: 12, padding: 12,
                    background: 'var(--surface)', display: 'flex', flexDirection: 'column', gap: 8,
                  }}>
                    {/* one problem shown once for the whole group */}
                    {group[0].what_happened && (
                      <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{group[0].what_happened}</div>
                    )}
                    {group[0].why_it_matters && (
                      <div style={{ fontSize: 12, color: 'var(--text-2)' }}><b>Почему важно:</b> {group[0].why_it_matters}</div>
                    )}
                    <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>Варианты решения:</div>
                    {group.map((i) => (
                      <DecisionFeedCard key={i.item_key} item={i} onChanged={onChanged}
                        roleLabel={roleLabel(i)} hideProblem />
                    ))}
                  </div>
                )
              ))}
            </div>
          )
        )}
      </div>
    </div>
  )
}

export default DecisionFeedPanel

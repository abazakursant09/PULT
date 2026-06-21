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
              {items.map((i) => (
                <DecisionFeedCard key={i.item_key} item={i} onChanged={onChanged} />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  )
}

export default DecisionFeedPanel

'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { DecisionFeedItem, PresentationCard, RecommendationGroup } from '@/lib/api'
import { DecisionFeedFilters } from './DecisionFeedFilters'
import { DecisionFeedCard } from './DecisionFeedCard'
import { DecisionFeedEmptyState } from './DecisionFeedEmptyState'
import { marketplaceLabel, recommendationsLabel } from '@/lib/feedGrouping'
import { patchItemState, removeItem, skipTopAction, ungroupedItems } from '@/lib/presentationFeed'

// "Что требует внимания сегодня" — главный видимый слой PULT. Список решений из
// всех контуров, не отчёт и не BI. Без рейтинга, без numeric priority, без прогноза.
//
// P5 — reads GET /api/presentation/cards (server-side grouping: product cards → deduped
// recommendation groups → items). The old client-side grouping is gone; mutations still run
// through api.decisionFeed on item_key (items nest verbatim).

type Action = 'seen' | 'snooze' | 'dismiss' | 'act'

// observed severity → conservative RU label. Not a score, verbatim class only.
const _SEV_LABEL: Record<string, string> = {
  critical: 'Критично', high: 'Высокий приоритет', medium: 'Средний приоритет', low: 'Низкий приоритет',
}
function severityLabel(s: string | null): string | null {
  return s ? (_SEV_LABEL[s] ?? s) : null
}

function roleLabel(it: DecisionFeedItem): string | null {
  if (it.action_role === 'primary') return 'Основной вариант'
  if (it.action_role === 'alternative') return 'Альтернатива'
  return null
}

export function DecisionFeedPanel({ skipTopAction: skip = false }: { skipTopAction?: boolean } = {}) {
  const [contour, setContour] = useState<string | null>(null)
  const [cards, setCards] = useState<PresentationCard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const resp = await api.presentation.getCards({ contour: contour ?? undefined, limit: 50 })
        if (alive) setCards(resp.cards)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [contour])

  // local update so the list reacts immediately after an action (operates over nested cards)
  function onChanged(itemKey: string, action: Action) {
    setCards((prev) => {
      if (action === 'snooze' || action === 'dismiss') return removeItem(prev, itemKey)
      return patchItemState(prev, itemKey, action === 'seen' ? 'seen' : 'acted')
    })
  }

  // De-dup with TodayFocus: it already shows the top_action (== build_feed[0]) above. Only in
  // the UNFILTERED view — a contour filter changes what's first and TodayFocus is unfiltered.
  const shown = skip && contour === null ? skipTopAction(cards) : cards

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
          shown.length === 0 ? (
            <DecisionFeedEmptyState />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {shown.map((card) => (
                <PresentationCardView key={card.group_key} card={card} onChanged={onChanged} />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  )
}

// ── one product card: header (sku · marketplace · severity + root-cause) → groups → rest ──
function PresentationCardView(
  { card, onChanged }: { card: PresentationCard; onChanged: (k: string, a: Action) => void },
) {
  const sev = severityLabel(card.highest_severity)
  const rest = ungroupedItems(card)

  const body = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {card.recommendation_groups.map((g) => (
        <RecommendationGroupView key={g.group_key} group={g} onChanged={onChanged} />
      ))}
      {/* items with no recommendation (never dropped) render as bare cards */}
      {rest.map((it) => (
        <DecisionFeedCard key={it.item_key} item={it} onChanged={onChanged} />
      ))}
    </div>
  )

  // sku-null cards have no product identity → render items bare (no header)
  if (!card.sku) return <div>{body}</div>

  return (
    <div style={{
      border: '1px solid var(--line)', borderRadius: 14, padding: 12,
      background: 'var(--bg, transparent)', display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>{card.sku}</span>
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>· {marketplaceLabel(card.marketplace)}</span>
        {sev && (
          <span style={{ fontSize: 11, color: 'var(--text-3)', border: '1px solid var(--line)',
            borderRadius: 6, padding: '1px 6px' }}>{sev}</span>
        )}
        <span style={{ fontSize: 11.5, color: 'var(--text-3)', marginLeft: 'auto' }}>
          {recommendationsLabel(card.items.length)}
        </span>
      </div>
      {card.root_cause_narrative && (
        <div style={{ fontSize: 12, color: 'var(--text-2)' }}>{card.root_cause_narrative}</div>
      )}
      {body}
    </div>
  )
}

// ── one deduped recommendation group ──────────────────────────────────────────
function RecommendationGroupView(
  { group, onChanged }: { group: RecommendationGroup; onChanged: (k: string, a: Action) => void },
) {
  // single item, nothing shared to hoist → render the card bare (keeps its own problem/action)
  if (group.items.length === 1) {
    return <DecisionFeedCard item={group.items[0]} onChanged={onChanged} />
  }
  const head = group.items[0]
  return (
    <div style={{
      border: '1px solid var(--line)', borderRadius: 12, padding: 12,
      background: 'var(--surface)', display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      {head.what_happened && (
        <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{head.what_happened}</div>
      )}
      {head.why_it_matters && (
        <div style={{ fontSize: 12, color: 'var(--text-2)' }}><b>Почему важно:</b> {head.why_it_matters}</div>
      )}
      <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>Варианты решения:</div>
      {group.items.map((i) => (
        <DecisionFeedCard key={i.item_key} item={i} onChanged={onChanged} roleLabel={roleLabel(i)} hideProblem />
      ))}
    </div>
  )
}

export default DecisionFeedPanel

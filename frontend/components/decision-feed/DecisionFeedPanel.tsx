'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { DecisionFeedItem, PresentationCard, RecommendationGroup } from '@/lib/api'
import { DecisionFeedFilters } from './DecisionFeedFilters'
import { DecisionFeedCard } from './DecisionFeedCard'
import { DecisionFeedEmptyState } from './DecisionFeedEmptyState'
import { marketplaceLabel, recommendationsLabel } from '@/lib/feedGrouping'
import { patchItemState, removeItem, skipTopAction, ungroupedItems } from '@/lib/presentationFeed'
import { severityLabel, severityBadgeVariant } from '@/lib/severity'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'

// "Что требует внимания сегодня" — главный видимый слой PULT. Список решений из
// всех контуров, не отчёт и не BI. Без рейтинга, без numeric priority, без прогноза.
//
// P5 — reads GET /api/presentation/cards (server-side grouping: product cards → deduped
// recommendation groups → items). The old client-side grouping is gone; mutations still run
// through api.decisionFeed on item_key (items nest verbatim).

type Action = 'seen' | 'snooze' | 'dismiss' | 'act'

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
    <Card variant="surface" className="mb-[18px] p-[18px]">
      <div className="mb-1">
        <h2 className="text-[16px] font-bold text-[var(--text)] m-0">Что требует внимания сегодня</h2>
        <div className="text-[12px] text-[var(--text-3)] mt-1">
          Собрано из SEO, рекламы, отзывов, роста, юридических рисков и доказанных эффектов решений.
        </div>
      </div>

      <div className="mt-3">
        <DecisionFeedFilters value={contour} onChange={setContour} />

        {loading && (
          <div className="flex flex-col gap-3.5" aria-label="Загрузка">
            {[0, 1].map((i) => (
              <div key={i} className="rounded-[14px] border border-[var(--line)] p-3 flex flex-col gap-2.5">
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-3.5 w-3/4" />
                <Skeleton className="h-8 w-full" />
              </div>
            ))}
          </div>
        )}
        {error && <div className="text-[12.5px] text-[var(--danger)]">Не удалось загрузить: {error}</div>}

        {!loading && !error && (
          shown.length === 0 ? (
            <DecisionFeedEmptyState />
          ) : (
            <div className="flex flex-col gap-3.5">
              {shown.map((card) => (
                <PresentationCardView key={card.group_key} card={card} onChanged={onChanged} />
              ))}
            </div>
          )
        )}
      </div>
    </Card>
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
    <Card variant="bordered" className="rounded-[14px] p-3 flex flex-col gap-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[13.5px] font-bold text-[var(--text)] leading-tight [font-variant-numeric:tabular-nums]">{card.sku}</span>
        <span className="text-[12px] text-[var(--text-3)]">· {marketplaceLabel(card.marketplace)}</span>
        {sev && (
          <Badge variant={severityBadgeVariant(card.highest_severity)} className="text-[10px] uppercase tracking-[0.2px] rounded-[5px]">
            {sev}
          </Badge>
        )}
        <span className="text-[11.5px] text-[var(--text-3)] ml-auto">
          {recommendationsLabel(card.items.length)}
        </span>
      </div>
      {card.root_cause_narrative && (
        <div className="text-[12px] leading-relaxed text-[var(--text-2)] px-2.5 py-2 rounded-[7px] bg-[var(--surface-h)] border-l-2 border-[var(--line)]">
          {card.root_cause_narrative}
        </div>
      )}
      {body}
    </Card>
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
  // Indented rule (not a nested solid box) — the shared problem is stated once, then the
  // variant cards sit under a light left rule so the hierarchy reads without box-in-box.
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 8,
      paddingLeft: 12, borderLeft: '2px solid var(--line)',
    }}>
      {head.what_happened && (
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', lineHeight: 1.35 }}>{head.what_happened}</div>
      )}
      {head.why_it_matters && (
        <div style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.4 }}><b>Почему важно:</b> {head.why_it_matters}</div>
      )}
      <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.2, color: 'var(--text-3)' }}>Варианты решения</div>
      {group.items.map((i) => (
        <DecisionFeedCard key={i.item_key} item={i} onChanged={onChanged} roleLabel={roleLabel(i)} hideProblem />
      ))}
    </div>
  )
}

export default DecisionFeedPanel

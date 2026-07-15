'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { TodayItem } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

// "Сегодня начни с этого" — the single #1 thing to do today, from the canonical Today
// source (/api/today → build_today → build_feed). Same source as the Telegram top action and
// Copilot. Shows ONLY response.top_action — no client sort, no client computation, no
// fabricated numbers. The full list lives in DecisionFeedPanel; this is the diagnosis HERO
// above it: what happened (money) → why → what to do → expected effect.

const _MP_NAME: Record<string, string> = {
  wb: 'Wildberries', wildberries: 'Wildberries', ozon: 'Ozon',
  yandex: 'Яндекс Маркет', yandex_market: 'Яндекс Маркет', megamarket: 'Megamarket',
}

export function TodayFocus() {
  const [top, setTop] = useState<TodayItem | null>(null)
  // P6 — additive root-cause narrative for the top action's product, read from the existing
  // Presentation API. top_action itself is UNCHANGED; this only enriches it.
  const [narrative, setNarrative] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null); setNarrative(null)
    ;(async () => {
      try {
        const [resp, cardsResp] = await Promise.all([
          api.today.get({ limit: 50 }),
          api.presentation.getCards({ limit: 50 }).catch(() => null),
        ])
        if (!alive) return
        setTop(resp.top_action)
        const ta = resp.top_action
        const card = ta && cardsResp
          ? cardsResp.cards.find((c) => c.marketplace === ta.marketplace && c.sku === ta.sku)
          : undefined
        setNarrative(card?.root_cause_narrative ?? null)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [])

  const headline = top ? (top.what_happened || top.title) : null
  const context = top
    ? [top.sku, top.marketplace ? (_MP_NAME[top.marketplace] || top.marketplace) : null]
        .filter(Boolean).join(' · ')
    : ''

  return (
    // The hero: an elevated surface with a thick violet spine, set apart from the quieter
    // panels below so the eye lands here first.
    <Card
      variant="elevated"
      className="mb-[18px] p-[18px] relative overflow-hidden"
      style={{ borderLeft: '3px solid var(--violet)' }}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] font-bold uppercase tracking-[0.1em] text-[var(--violet-text)]">
          Сегодня начни с этого
        </span>
      </div>

      <div className="mt-3">
        {loading && (
          <div className="flex flex-col gap-2.5" aria-label="Загрузка">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
        {error && !loading && (
          <div className="text-[12.5px] text-[var(--danger)]">Не удалось загрузить: {error}</div>
        )}

        {!loading && !error && (
          top === null ? (
            <div className="text-[13px] text-[var(--text-3)]">Рекомендации появятся, когда PULT получит данные.</div>
          ) : (
            <div className="flex flex-col gap-2">
              {/* what happened — the diagnosis headline, the biggest text on the panel */}
              {headline && (
                <div className="text-[18px] font-bold leading-snug text-[var(--text)]">{headline}</div>
              )}
              {context && <div className="text-[12px] text-[var(--text-3)]">{context}</div>}

              {/* what to do — the obvious action, rendered as the hero's primary call */}
              {top!.recommended_action && (
                <div className="mt-1 inline-flex items-start gap-2 rounded-[var(--r-sm)] border border-[var(--violet)] bg-[var(--violet-dim)] px-3 py-2 self-start">
                  <span className="text-[10px] font-bold uppercase tracking-wide text-[var(--violet-text)] mt-0.5 shrink-0">Что сделать</span>
                  <span className="text-[13px] font-semibold text-[var(--text)]">{top!.recommended_action}</span>
                </div>
              )}

              {top!.why_it_matters && (
                <div className="text-[12px] text-[var(--text-2)] mt-1">
                  <b>Почему важно:</b> {top!.why_it_matters}
                </div>
              )}
              {top!.expected_effect && (
                <div className="text-[12px] text-[var(--text-2)]">
                  <b>Что это даст:</b> {top!.expected_effect}
                </div>
              )}
              {/* P6 — additive root-cause narrative. Only when the product has a converging one. */}
              {narrative && (
                <div className="text-[12px] leading-relaxed text-[var(--text-2)] rounded-[7px] px-2.5 py-2 bg-[var(--surface-h)] border-l-2 border-[var(--line)]">
                  {narrative}
                </div>
              )}
            </div>
          )
        )}
      </div>
    </Card>
  )
}

export default TodayFocus

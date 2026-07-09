'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { TodayItem } from '@/lib/api'

// "Сегодня начни с этого" — the single #1 thing to do today, from the canonical Today
// source (/api/today → build_today → build_feed). Same source as the Telegram top
// action and Copilot. Shows ONLY response.top_action — no client sort, no client
// computation, no fabricated numbers, no mock data. The full list lives in
// DecisionFeedPanel; this is the "start here" hero above it.

const _MP_NAME: Record<string, string> = {
  wb: 'Wildberries', wildberries: 'Wildberries', ozon: 'Ozon',
  yandex: 'Яндекс Маркет', yandex_market: 'Яндекс Маркет', megamarket: 'Megamarket',
}

export function TodayFocus() {
  const [top, setTop] = useState<TodayItem | null>(null)
  // P6 — additive root-cause narrative for the top action's product, read from the existing
  // Presentation API (GET /api/presentation/cards). top_action itself is UNCHANGED (still
  // /api/today, build_feed[0]); this only enriches it. Presentation fetch failing never
  // breaks Today — narrative stays null.
  const [narrative, setNarrative] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null); setNarrative(null)
    ;(async () => {
      try {
        // top_action from /api/today (canonical, unchanged); cards from the existing
        // Presentation API in parallel. Cards are best-effort — a failure must not break Today.
        const [resp, cardsResp] = await Promise.all([
          api.today.get({ limit: 50 }),
          api.presentation.getCards({ limit: 50 }).catch(() => null),
        ])
        if (!alive) return
        setTop(resp.top_action)
        const ta = resp.top_action
        // match the card by the SAME raw (marketplace, sku) the top action carries — the
        // presentation cards group on those exact values, so equality is exact.
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
    <div className="s-card" style={{ marginBottom: 18, borderLeft: '3px solid var(--violet, #6e6afc)' }}>
      <div style={{ marginBottom: 4 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
          Сегодня начни с этого
        </h2>
      </div>

      <div style={{ marginTop: 12 }}>
        {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка…</div>}
        {error && !loading && (
          <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить: {error}</div>
        )}

        {!loading && !error && (
          top === null ? (
            <div style={{ fontSize: 13, color: 'var(--text-3)' }}>Рекомендации появятся, когда PULT получит данные.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {headline && (
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{headline}</div>
              )}
              {context && (
                <div style={{ fontSize: 12, color: 'var(--text-3)' }}>{context}</div>
              )}
              {top!.recommended_action && (
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--violet-text, var(--text))' }}>
                  {top!.recommended_action}
                </div>
              )}
              {top!.why_it_matters && (
                <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                  <b>Почему важно:</b> {top!.why_it_matters}
                </div>
              )}
              {top!.expected_effect && (
                <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
                  <b>Что это даст:</b> {top!.expected_effect}
                </div>
              )}
              {/* P6 — additive root-cause narrative (P5.1 explanation-block style). Only when
                  the top action's product has a converging narrative; otherwise nothing extra. */}
              {narrative && (
                <div style={{
                  fontSize: 12, color: 'var(--text-2)', lineHeight: 1.45,
                  padding: '8px 10px', borderRadius: 7,
                  background: 'var(--surface-h)', borderLeft: '2px solid var(--line)',
                }}>{narrative}</div>
              )}
            </div>
          )
        )}
      </div>
    </div>
  )
}

export default TodayFocus

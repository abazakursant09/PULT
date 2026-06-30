'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { TodaySummary } from '@/lib/api'

// "Состояние бизнеса сегодня" — the first thing a seller sees, above Today Focus.
// Read-only assembly of EXISTING aggregates from GET /api/today/summary. No charts,
// no BI, no client computation, no fabricated numbers — every value is rendered
// verbatim from the response.

function _rub(n: number): string {
  return `${Math.round(n).toLocaleString('ru-RU')} ₽`
}
function _delta(pct: number | null): string {
  if (pct === null) return '—'
  const s = pct > 0 ? '+' : ''
  return `${s}${pct}%`
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' | 'muted' }) {
  const color = tone === 'pos' ? 'var(--gain, #16a34a)'
    : tone === 'neg' ? 'var(--danger, #ef4444)'
    : 'var(--text)'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
      <span style={{ fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{label}</span>
      <span style={{ fontSize: 15, fontWeight: 700, color }}>{value}</span>
    </div>
  )
}

export function BusinessToday() {
  const [s, setS] = useState<TodaySummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const resp = await api.today.getSummary()
        if (alive) setS(resp)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [])

  return (
    <div className="s-card" style={{ marginBottom: 18 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
          Состояние бизнеса сегодня
        </h2>
        {s?.is_demo && (
          <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.08em', color: 'var(--violet-text, var(--text-3))',
            background: 'rgba(110,106,252,0.12)', padding: '1px 6px', borderRadius: 3 }}>
            ДЕМО
          </span>
        )}
      </div>

      {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка…</div>}
      {error && !loading && (
        <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить: {error}</div>
      )}

      {!loading && !error && s && (
        !s.has_data ? (
          <div style={{ fontSize: 13, color: 'var(--text-3)' }}>Недостаточно данных за сегодня</div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px 28px' }}>
            <Metric label="Выручка" value={_rub(s.revenue_today)} />
            <Metric label="Прибыль" value={_rub(s.profit_today)} tone={s.profit_today < 0 ? 'neg' : 'pos'} />
            <Metric label="Маржа" value={s.margin_pct === null ? '—' : `${s.margin_pct}%`} />
            <Metric label="Изменение к вчера" value={_delta(s.delta_revenue_pct)}
              tone={s.delta_revenue_pct === null ? 'muted' : s.delta_revenue_pct < 0 ? 'neg' : 'pos'} />
            <Metric label="Критичных проблем" value={String(s.critical_count)}
              tone={s.critical_count > 0 ? 'neg' : 'muted'} />
            <Metric label="Товаров с убытком" value={String(s.loss_products_count)}
              tone={s.loss_products_count > 0 ? 'neg' : 'muted'} />
            <Metric label="Возможностей роста" value={String(s.growth_opportunities_count)}
              tone={s.growth_opportunities_count > 0 ? 'pos' : 'muted'} />
            <Metric label="Критичный остаток" value={String(s.low_stock_count)}
              tone={s.low_stock_count > 0 ? 'neg' : 'muted'} />
          </div>
        )
      )}
    </div>
  )
}

export default BusinessToday

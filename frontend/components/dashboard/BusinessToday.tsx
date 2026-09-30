'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { TodaySummary } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Boxes, Search, ShoppingCart, Coins } from 'lucide-react'
import styles from './cabinet.module.css'

// "Состояние бизнеса сегодня" — the business-state strip under the diagnosis hero.
// Read-only assembly of EXISTING aggregates from GET /api/today/summary. No charts, no BI,
// no client computation, no fabricated numbers — every value is rendered verbatim.
//
// P2 hierarchy: profit is the dominant figure (it answers "am I making money"); revenue,
// margin, delta and loss-count are the secondary ring. Figures use the mono stack for aligned
// tabular digits so the numbers read as a set, not a jumble.

function _rub(n: number): string {
  return `${Math.round(n).toLocaleString('ru-RU')} ₽`
}
function _delta(pct: number | null): string {
  if (pct === null) return '—'
  const s = pct > 0 ? '+' : ''
  return `${s}${pct}%`
}

// The hero figure — bigger, mono, coloured by sign.
function HeroMetric({ label, value, tone }: { label: string; value: string; tone: 'pos' | 'neg' }) {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <span className="text-[11px] uppercase tracking-wide text-[var(--text-3)]">{label}</span>
      <span
        className="text-[28px] font-bold leading-none [font-variant-numeric:tabular-nums]"
        style={{ color: tone === 'neg' ? 'var(--danger)' : 'var(--success)', fontFamily: 'var(--font-mono)' }}
      >
        {value}
      </span>
    </div>
  )
}

// Secondary figures — smaller, quieter, still tabular.
function Metric({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' | 'muted' }) {
  const color = tone === 'pos' ? 'var(--success)' : tone === 'neg' ? 'var(--danger)' : 'var(--text)'
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[11px] text-[var(--text-3)] whitespace-nowrap">{label}</span>
      <span className="text-[15px] font-semibold [font-variant-numeric:tabular-nums]" style={{ color, fontFamily: 'var(--font-mono)' }}>
        {value}
      </span>
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
    <div className={styles.overview}>
    <Card variant="surface" className={styles.contour}>
      <div className={styles.sectionHead}>
        <div>
          <span className={styles.caption}>Состояние бизнеса сегодня</span>
          <h2>Операционный контур</h2>
        </div>
        <div className={styles.period}>
          <span>Сегодня · выручка к вчера</span>
          {s?.is_demo && <Badge variant="neutral">ДЕМО</Badge>}
        </div>
      </div>

      {loading && (
        <div className="flex flex-wrap gap-x-7 gap-y-3.5" aria-label="Загрузка">
          <Skeleton className="h-[34px] w-[160px]" />
          <Skeleton className="h-[34px] w-[90px]" />
          <Skeleton className="h-[34px] w-[90px]" />
          <Skeleton className="h-[34px] w-[120px]" />
        </div>
      )}
      {error && !loading && (
        <div className="text-[12.5px] text-[var(--danger)]">Не удалось загрузить: {error}</div>
      )}

      {!loading && !error && s && (
        !s.has_data ? (
          <div className="text-[13px] text-[var(--text-3)]">Недостаточно данных за сегодня</div>
        ) : (
          <div>
            <div className={styles.flow}>
              <article className={styles.node}>
                <h3><Search size={24} aria-hidden="true" />Спрос</h3>
                <span className={styles.caption}>Сигналы роста</span>
                <strong>{s.growth_opportunities_count}</strong>
                <span className={styles.nodeNote}>По доступным данным</span>
              </article>
              <article className={styles.node}>
                <h3><ShoppingCart size={24} aria-hidden="true" />Продажи</h3>
                <span className={styles.caption}>Выручка</span>
                <strong>{_rub(s.revenue_today)}</strong>
                <span className={styles.nodeNote}>За сегодня</span>
              </article>
              <article className={styles.node}>
                <h3><Coins size={24} aria-hidden="true" />Маржа</h3>
                <span className={styles.caption}>Маржинальность</span>
                <strong>{s.margin_pct === null ? '—' : `${s.margin_pct}%`}</strong>
                <span className={styles.nodeNote}>За сегодня</span>
              </article>
              <article className={styles.node}>
                <h3><Boxes size={24} aria-hidden="true" />Остатки</h3>
                <span className={styles.caption}>Товаров с низким остатком</span>
                <strong>{s.low_stock_count}</strong>
                <span className={styles.nodeNote}>По доступным данным</span>
              </article>
            </div>
            <div className={styles.facts}>
            <HeroMetric label="Прибыль сегодня" value={_rub(s.profit_today)} tone={s.profit_today < 0 ? 'neg' : 'pos'} />
            <Metric label="Изменение к вчера" value={_delta(s.delta_revenue_pct)}
              tone={s.delta_revenue_pct === null ? 'muted' : s.delta_revenue_pct < 0 ? 'neg' : 'pos'} />
            <Metric label="Товаров с убытком" value={String(s.loss_products_count)}
              tone={s.loss_products_count > 0 ? 'neg' : 'muted'} />
            </div>
          </div>
        )
      )}
    </Card>
    {!loading && !error && s?.has_data && (
      <aside className={styles.signals} aria-label="Сигналы по данным">
        <h2>Сигналы</h2>
        <p className={styles.caption}>По доступным данным{s.is_demo ? ' · демо' : ''}</p>
        <dl>
          <div><dt>Критические решения</dt><dd>{s.critical_count}</dd></div>
          <div><dt>Товары с низким остатком</dt><dd>{s.low_stock_count}</dd></div>
          <div><dt>Сигналы роста</dt><dd>{s.growth_opportunities_count}</dd></div>
        </dl>
        <p className={styles.caption}>Подробности и доступные действия — в разделе решений.</p>
      </aside>
    )}
    </div>
  )
}

export default BusinessToday

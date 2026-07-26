'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type StoreFinanceSummaryOut } from '@/lib/api'
import { CompletenessNote, ConflictBanner } from './Completeness'

// Store financial summary (PULT-LAUNCH-1.4.5I-QA2). Shows the ONE resolved money total the backend
// computes for this store, how it was sourced (API vs CSV), and — only when the backend reports it —
// an API-vs-CSV REVENUE conflict with both real values. The conflict is about revenue, not every
// metric: resolving it writes the source policy for metric_type='revenue' and re-reads the summary.
// A null profit is shown as «Недостаточно данных для расчёта», never 0.

function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ₽'
}

export function StoreFinanceSummary({ storeId }: { storeId: string }) {
  const [data, setData] = useState<StoreFinanceSummaryOut | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [resolving, setResolving] = useState(false)
  const [resolveError, setResolveError] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await api.marketplaceStores.financeSummary(storeId))
      setState('ready')
    } catch {
      setState('failed')
    }
  }, [storeId])

  useEffect(() => { void load() }, [load])

  // Resolve the revenue conflict by choosing a source. Nothing shows success until the PATCH AND the
  // re-read of the summary both come back — the seller sees the real, resolved number, never a guess.
  const choose = async (source: 'api' | 'csv') => {
    setResolving(true); setResolveError('')
    try {
      await api.sourcePolicy.set(storeId, 'revenue', source)
      await load()
    } catch {
      // No false success: the conflict stays, the safe value stands, and the seller is told to retry.
      setResolveError('Не удалось сохранить выбор источника. Повторите.')
    } finally {
      setResolving(false)
    }
  }

  if (state === 'loading') return <p className="l-dim" style={{ padding: '10px 0' }}>Загружаем финансовый итог…</p>
  if (state === 'failed' || !data) {
    return <p className="l-dim" style={{ padding: '10px 0' }}>Не удалось загрузить финансовый итог. Обновите страницу.</p>
  }

  const profitKnown = data.net_profit !== null && data.net_profit !== undefined

  return (
    <div>
      <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap', alignItems: 'baseline', padding: '4px 0 2px' }}>
        <div>
          <div className="l-caps l-muted" style={{ marginBottom: 4 }}>Выручка</div>
          <div className="l-serif" style={{ fontSize: 26 }}>{money(data.revenue)}</div>
        </div>
        <div>
          <div className="l-caps l-muted" style={{ marginBottom: 4 }}>Прибыль</div>
          <div className="l-serif" style={{ fontSize: 26 }}>
            {profitKnown ? money(data.net_profit)
              : <span style={{ fontSize: 16 }} className="l-dim">Недостаточно данных для расчёта</span>}
          </div>
        </div>
      </div>

      <p className="l-dim" style={{ padding: '8px 0 0', fontSize: 13.5 }}>
        Источник финансового расчёта магазина: {data.source === 'api' ? 'API' : 'CSV'}.
      </p>

      <CompletenessNote completeness={data.completeness} missingFields={data.missing_fields} />

      {data.conflict && data.conflict_candidates && (
        <>
          <ConflictBanner
            metricLabel="Выручка"
            apiValue={money(data.conflict_candidates.api)}
            csvValue={money(data.conflict_candidates.csv)}
            chosen={data.source}
            busy={resolving}
            onChoose={choose}
          />
          {resolveError && <p className="l-src-note l-src-note--err" role="alert">{resolveError}</p>}
        </>
      )}
    </div>
  )
}

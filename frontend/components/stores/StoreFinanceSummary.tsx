'use client'

import { useCallback } from 'react'
import { api, type StoreFinanceSummaryOut } from '@/lib/api'
import { CompletenessNote, ConflictBanner } from './Completeness'
import { useConfirmedStoreRead, useStoreSourceSync } from './StoreSourceSync'

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
  const { revision, busy, change, outcome: currentOutcome } = useStoreSourceSync(storeId)
  const request = useCallback(() => api.marketplaceStores.financeSummary(storeId), [storeId])
  const { data, state, reload } = useConfirmedStoreRead<StoreFinanceSummaryOut>(storeId, revision, 'finance', request)

  // Resolve the revenue conflict by choosing a source. Nothing shows success until the PATCH AND the
  // re-read of the summary both come back — the seller sees the real, resolved number, never a guess.
  const choose = (source: 'api' | 'csv') => change('revenue', source)

  if (state === 'loading') return <p className="l-dim" role="status" style={{ padding: '10px 0' }}>Загружаем финансовый итог…</p>
  if (state === 'failed' || !data) {
    return <div style={{ padding: '10px 0' }}>
      <p className="l-dim" role="alert">{currentOutcome === 'confirmed'
        ? 'Выбор сохранён, но не удалось загрузить финансовый итог. Повторите загрузку.'
        : currentOutcome === 'unknown'
          ? 'Не удалось подтвердить сохранение и загрузить финансовый итог. Повторите загрузку.'
          : 'Не удалось загрузить финансовый итог. Повторите загрузку.'}</p>
      <button type="button" className="l-btn" onClick={() => void reload()}>Повторить</button>
    </div>
  }

  const profitKnown = data.net_profit !== null && data.net_profit !== undefined

  return (
    <div>
      {currentOutcome === 'unknown' && <p className="l-src-note l-src-note--err" role="alert">
        Не удалось подтвердить сохранение по ответу сервера. Показаны перечитанные данные.
      </p>}
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
            busy={busy}
            onChoose={choose}
          />
        </>
      )}
    </div>
  )
}

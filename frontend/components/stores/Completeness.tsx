'use client'

// Honest completeness + conflict surfaces (PULT-LAUNCH-1.4.5I, §7–§9).
//
// These are prop-driven and reusable: a caller passes what the backend actually returned. They never
// invent data — a missing value is shown as "нет данных", never as 0, and an exact profit is never
// shown while a required input (cost of goods, ad spend) is missing. The conflict banner offers the
// real resolution: choosing a source writes the source policy (a PATCH), it never sums the two.

export type Completeness = 'complete' | 'incomplete' | 'no_data'

export function CompletenessNote({ completeness, missingFields = [] }: {
  completeness: Completeness
  missingFields?: string[]
}) {
  if (completeness === 'no_data') {
    return <p className="l-src-note">Нет данных.</p>
  }
  if (completeness !== 'incomplete') return null
  const parts: string[] = []
  if (missingFields.includes('cogs')) parts.push('Нет данных о себестоимости. Прибыль и маржа не рассчитаны.')
  if (missingFields.includes('ad_spend')) parts.push('Нет данных о рекламных расходах. ДРР не рассчитан.')
  if (missingFields.includes('product_attribution')) {
    parts.push('Часть операций не удалось связать с товарами. Итоги магазина полные, а показатели отдельных товаров — неполные.')
  }
  if (parts.length === 0) parts.push('Данные неполные.')
  return (
    <div className="l-src-note" role="note">
      {parts.map((t, i) => <p key={i} style={{ margin: i ? '4px 0 0' : 0 }}>{t}</p>)}
    </div>
  )
}

// A source conflict is NOT an import (SKU) conflict — different screen, different cause. This one is
// about a value the API and CSV both report differently for the same store metric and period.
export function ConflictBanner({ metricLabel, period, apiValue, csvValue, chosen, onChoose, busy }: {
  metricLabel: string
  period?: string
  apiValue: string
  csvValue: string
  chosen: 'api' | 'csv'
  onChoose: (source: 'api' | 'csv') => void | Promise<void>
  busy?: boolean
}) {
  return (
    <div className="l-conflict" role="alert">
      <p className="l-conflict-title">
        Есть расхождение API и CSV{period ? ` · ${period}` : ''}
      </p>
      <p className="l-src-note" style={{ margin: '2px 0 8px' }}>
        API и CSV содержат разные значения показателя «{metricLabel}». До вашего решения используется CSV.
      </p>
      <div className="l-conflict-vals">
        <span>API: <b>{apiValue}</b></span>
        <span>CSV: <b>{csvValue}</b></span>
        <span className="l-dim">Сейчас: {chosen === 'api' ? 'API' : 'CSV'}</span>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button type="button" className="l-btn-ink" disabled={busy} onClick={() => void onChoose('api')}>
          Использовать API
        </button>
        <button type="button" className="l-btn" disabled={busy} onClick={() => void onChoose('csv')}>
          Использовать CSV
        </button>
      </div>
    </div>
  )
}

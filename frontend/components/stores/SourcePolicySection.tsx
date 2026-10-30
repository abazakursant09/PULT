'use client'

import { useCallback } from 'react'
import { api, type SourcePolicyMetric, type SourcePolicyOut } from '@/lib/api'
import { useConfirmedStoreRead, useStoreSourceSync } from './StoreSourceSync'

// Источники данных (PULT-LAUNCH-1.4.5I). Per (store, metric) the seller chooses where the numbers
// come from — API, CSV, or Auto. The backend is the source of truth: this reads the EFFECTIVE policy
// (absent ⇒ CSV) and writes only explicit choices. It never fabricates an 'auto' default on open.
//
// Thirteen technical metrics are grouped into four plain-language sections so the seller reads intent,
// not a table of internals. An option is disabled — never hidden with a false-success — when the API
// cannot honestly source that metric (unverified connection, unsupported data type, Yandex finance,
// or a value the marketplace never knows: cost of goods and ad spend).

type Group = { title: string; hint?: string; metrics: { key: string; label: string }[] }

const GROUPS: Group[] = [
  {
    title: 'Каталог',
    metrics: [
      { key: 'catalog', label: 'Товары' },
      { key: 'card_content', label: 'Карточки' },
      { key: 'price', label: 'Цены' },
      { key: 'stock', label: 'Остатки' },
    ],
  },
  {
    title: 'Операции',
    metrics: [
      { key: 'orders', label: 'Заказы' },
      { key: 'returns', label: 'Возвраты' },
    ],
  },
  {
    title: 'Деньги',
    metrics: [
      { key: 'revenue', label: 'Выручка' },
      { key: 'marketplace_fees', label: 'Комиссии' },
      { key: 'logistics', label: 'Логистика' },
      { key: 'penalties', label: 'Штрафы' },
      { key: 'deductions', label: 'Удержания' },
    ],
  },
  {
    title: 'Данные продавца',
    hint: 'Маркетплейс не знает эти данные. Используйте CSV или ручной ввод.',
    metrics: [
      { key: 'cogs', label: 'Себестоимость' },
      { key: 'ad_spend', label: 'Рекламные расходы' },
    ],
  },
]

const PREF_LABEL: Record<string, string> = { auto: 'Автоматически', api: 'Только API', csv: 'Только CSV' }
const PREF_ORDER: ('auto' | 'api' | 'csv')[] = ['auto', 'api', 'csv']

function limitationText(m: SourcePolicyMetric): string | null {
  switch (m.limitation) {
    case 'yandex_finance_unsupported':
      return 'Финансовая синхронизация Яндекс Маркета пока недоступна. Загружайте эти данные через CSV.'
    case 'manual_only_csv':
      return null // shown once at group level
    default:
      return null
  }
}

function MetricRow({ metric, label, busy, onChoose }: {
  metric: SourcePolicyMetric
  label: string
  busy: boolean
  onChoose: (metric: string, preference: 'auto' | 'api' | 'csv') => Promise<void>
}) {
  const apiDisabled = !metric.api_supported

  const choose = async (pref: 'auto' | 'api' | 'csv') => {
    if (pref === metric.preference || busy) return
    await onChoose(metric.metric_type, pref)
  }

  return (
    <div className="l-src-row">
      <div className="l-src-label">
        <span>{label}</span>
        {metric.preference === 'api' && metric.api_available && (
          <span className="l-src-tag">API</span>
        )}
        {metric.preference === 'csv' && <span className="l-src-tag l-src-tag--dim">CSV</span>}
      </div>
      <div className="l-src-choices" role="radiogroup" aria-label={label}>
        {PREF_ORDER.map(pref => {
          const disabled = busy || (pref === 'api' && apiDisabled)
          const active = metric.preference === pref
          return (
            <button
              key={pref}
              type="button"
              role="radio"
              aria-checked={active}
              className={`l-src-opt${active ? ' l-src-opt--on' : ''}`}
              disabled={disabled}
              onClick={() => void choose(pref)}
            >
              {PREF_LABEL[pref]}
            </button>
          )
        })}
      </div>
      {limitationText(metric) && <p className="l-src-note">{limitationText(metric)}</p>}
    </div>
  )
}

export function SourcePolicySection({ storeId, marketplace }: { storeId: string; marketplace: string }) {
  const { revision, busy, change, outcome: currentOutcome } = useStoreSourceSync(storeId)
  const request = useCallback(() => api.sourcePolicy.get(storeId), [storeId])
  const { data: policy, state, reload } = useConfirmedStoreRead<SourcePolicyOut>(storeId, revision, 'policy', request)

  if (state === 'loading') return <p className="l-dim" role="status" style={{ padding: '10px 0' }}>Загружаем источники…</p>
  if (state === 'failed' || !policy) {
    return (
      <div style={{ padding: '10px 0' }}>
        <p className="l-dim" role="alert">{currentOutcome === 'confirmed'
          ? 'Выбор сохранён, но не удалось загрузить источники данных. Повторите загрузку.'
          : currentOutcome === 'unknown'
            ? 'Не удалось подтвердить сохранение и загрузить источники данных. Повторите загрузку.'
            : 'Не удалось загрузить источники данных. Повторите загрузку.'}</p>
        <button type="button" className="l-btn" onClick={() => void reload()}>Повторить</button>
      </div>
    )
  }

  const byKey = new Map(policy.metrics.map(m => [m.metric_type, m]))

  return (
    <div>
      {currentOutcome === 'unknown' && <p className="l-src-note l-src-note--err" role="alert">
        Не удалось подтвердить сохранение по ответу сервера. Показаны перечитанные данные.
      </p>}
      <p className="l-dim" style={{ padding: '4px 0 14px', maxWidth: '60ch' }}>
        Выберите, откуда PULT берёт каждый показатель. «Автоматически» использует API только при
        полном покрытии данных, иначе — загруженные файлы. Одинаковые показатели не складываются.
      </p>
      {GROUPS.map(group => (
        <section key={group.title} className="l-src-group">
          <h3 className="l-src-group-title">{group.title}</h3>
          {group.hint && <p className="l-src-note l-src-note--group">{group.hint}</p>}
          {group.metrics.map(({ key, label }) => {
            const m = byKey.get(key)
            if (!m) return null
            return <MetricRow key={key} metric={m} label={label} busy={busy} onChoose={change} />
          })}
        </section>
      ))}
    </div>
  )
}

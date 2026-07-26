'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type SourcePolicyMetric, type SourcePolicyOut } from '@/lib/api'

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

function MetricRow({ storeId, metric, label, onChanged }: {
  storeId: string
  metric: SourcePolicyMetric
  label: string
  onChanged: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const apiDisabled = !metric.api_supported

  const choose = async (pref: 'auto' | 'api' | 'csv') => {
    if (pref === metric.preference || busy) return
    setBusy(true); setError('')
    try {
      await api.sourcePolicy.set(storeId, metric.metric_type, pref)
      await onChanged()
    } catch {
      setError('Не удалось сохранить выбор. Повторите.')
    } finally {
      setBusy(false)
    }
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
      {error && <p className="l-src-note l-src-note--err">{error}</p>}
    </div>
  )
}

export function SourcePolicySection({ storeId, marketplace }: { storeId: string; marketplace: string }) {
  const [policy, setPolicy] = useState<SourcePolicyOut | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')

  const load = useCallback(async () => {
    try {
      setPolicy(await api.sourcePolicy.get(storeId))
      setState('ready')
    } catch {
      setState('failed')
    }
  }, [storeId])

  useEffect(() => { void load() }, [load])

  if (state === 'loading') return <p className="l-dim" style={{ padding: '10px 0' }}>Загружаем источники…</p>
  if (state === 'failed' || !policy) {
    return (
      <p className="l-dim" style={{ padding: '10px 0' }}>
        Не удалось загрузить источники данных. Обновите страницу.
      </p>
    )
  }

  const byKey = new Map(policy.metrics.map(m => [m.metric_type, m]))

  return (
    <div>
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
            return <MetricRow key={key} storeId={storeId} metric={m} label={label} onChanged={load} />
          })}
        </section>
      ))}
    </div>
  )
}

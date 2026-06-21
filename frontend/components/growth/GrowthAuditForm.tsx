'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { GrowthAuditResult, GrowthThresholdsIn } from '@/lib/api'

// MVP trigger form: sku + marketplace + thresholds → POST /api/growth/audit.
// growth_unavailable is shown as a calm informational note, NOT an error.
// No prediction, no fabricated index — only a request to surface opportunities.

const MARKETPLACES = [
  { v: 'wildberries', l: 'Wildberries' },
  { v: 'ozon', l: 'Ozon' },
  { v: 'yandex', l: 'Яндекс Маркет' },
]

const inp: React.CSSProperties = {
  width: '100%', padding: '8px 10px', fontSize: 12.5, borderRadius: 8,
  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text)',
}
const lab: React.CSSProperties = { fontSize: 11, color: 'var(--text-3)', marginBottom: 4, display: 'block' }

type Outcome =
  | { kind: 'idle' }
  | { kind: 'unavailable' }
  | { kind: 'done'; result: GrowthAuditResult }
  | { kind: 'error'; message: string }

function _num(v: string): number | undefined {
  const t = v.trim()
  if (!t) return undefined
  const n = Number(t)
  return Number.isFinite(n) ? n : undefined
}

export function GrowthAuditForm(
  { listingId, marketplace, sku, onDone }:
  { listingId?: string; marketplace?: string; sku?: string; onDone?: () => void },
) {
  const [skuV, setSkuV] = useState(sku ?? '')
  const [mp, setMp] = useState(marketplace ?? 'wildberries')
  const [lowStock, setLowStock] = useState('')
  const [minRevenue, setMinRevenue] = useState('')
  const [minProfit, setMinProfit] = useState('')
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit() {
    if (!skuV.trim() || busy) return
    setBusy(true); setOutcome({ kind: 'idle' })
    const thresholds: GrowthThresholdsIn = {
      low_stock_units: _num(lowStock),
      min_revenue_for_growth_signal: _num(minRevenue),
      min_net_profit_for_growth_signal: _num(minProfit),
    }
    try {
      const result = await api.growth.runGrowthAudit({
        listing_id: listingId, marketplace: mp, sku: skuV.trim(), thresholds,
      })
      if (result.status === 'growth_unavailable' || result.ok === false) {
        setOutcome({ kind: 'unavailable' })   // not an error
      } else {
        setOutcome({ kind: 'done', result })
        onDone?.()
      }
    } catch (e) {
      setOutcome({ kind: 'error', message: e instanceof Error ? e.message : 'Не удалось выполнить проверку' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12,
      padding: 14, display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 140 }}>
          <label style={lab}>SKU товара</label>
          <input style={inp} value={skuV} onChange={(e) => setSkuV(e.target.value)} placeholder="например, SKU1" />
        </div>
        <div style={{ flex: 1, minWidth: 140 }}>
          <label style={lab}>Маркетплейс</label>
          <select style={inp} value={mp} onChange={(e) => setMp(e.target.value)}>
            {MARKETPLACES.map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}
          </select>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 120 }}>
          <label style={lab}>Низкий остаток (шт)</label>
          <input style={inp} value={lowStock} onChange={(e) => setLowStock(e.target.value)} inputMode="numeric" placeholder="напр. 5" />
        </div>
        <div style={{ flex: 1, minWidth: 120 }}>
          <label style={lab}>Мин. выручка</label>
          <input style={inp} value={minRevenue} onChange={(e) => setMinRevenue(e.target.value)} inputMode="numeric" placeholder="напр. 1000" />
        </div>
        <div style={{ flex: 1, minWidth: 120 }}>
          <label style={lab}>Мин. прибыль</label>
          <input style={inp} value={minProfit} onChange={(e) => setMinProfit(e.target.value)} inputMode="numeric" placeholder="напр. 100" />
        </div>
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--text-3)' }}>
        Пороги нужны, чтобы проверить гипотезу роста. Без порога часть возможностей будет не оценена.
      </div>
      <button onClick={submit} disabled={busy || !skuV.trim()} style={{
        alignSelf: 'flex-start', padding: '8px 14px', fontSize: 12.5, fontWeight: 600,
        borderRadius: 8, border: '1px solid var(--line)',
        background: busy ? 'var(--surface)' : 'var(--surface-h)',
        color: 'var(--text)', cursor: busy || !skuV.trim() ? 'default' : 'pointer',
        opacity: busy || !skuV.trim() ? 0.6 : 1,
      }}>{busy ? 'Ищем…' : 'Найти возможности роста'}</button>

      {outcome.kind === 'unavailable' && (
        <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
          Не удалось найти возможности роста: нет финансовых данных по этому товару.
        </div>
      )}
      {outcome.kind === 'done' && (
        <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
          Проверка выполнена. Найдено возможностей: {outcome.result.total_problems ?? 0}.
        </div>
      )}
      {outcome.kind === 'error' && (
        <div style={{ fontSize: 12, color: 'var(--danger)' }}>Ошибка: {outcome.message}</div>
      )}
    </div>
  )
}

export default GrowthAuditForm

'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { AdvAuditPayload, AdvThresholds, AdvAuditResult } from '@/lib/api'

/**
 * AdvertisingAuditForm — запуск рекламного аудита. Данные берутся из импорта
 * финансов по SKU; пороги площадки опциональны (если не заданы — часть рисков
 * честно остаётся «не оценено», пороги не выдумываются). Без CTR/CPC/кампаний.
 */
function num(v: string): number | undefined {
  const n = Number(v)
  return v.trim() === '' || Number.isNaN(n) ? undefined : n
}

const inp: React.CSSProperties = { width: '100%', padding: '8px 10px', fontSize: 12.5, borderRadius: 8, border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text)' }
const lab: React.CSSProperties = { fontSize: 11, color: 'var(--text-3)', marginBottom: 4, display: 'block' }

const TH_FIELDS: { key: keyof AdvThresholds; label: string }[] = [
  { key: 'max_drr', label: 'Макс. ДРР, %' },
  { key: 'min_revenue_for_signal', label: 'Мин. выручка, ₽' },
  { key: 'min_ad_spend_for_signal', label: 'Мин. расход, ₽' },
  { key: 'low_margin_threshold', label: 'Низкая маржа, %' },
  { key: 'low_stock_units', label: 'Низкий остаток, шт' },
  { key: 'oos_risk_days', label: 'Риск OOS, дней' },
]

export function AdvertisingAuditForm(
  { listingId, marketplace, onDone }: { listingId: string; marketplace: string; onDone: () => void },
) {
  const [sku, setSku] = useState('')
  const [th, setTh] = useState<Record<string, string>>({})
  const [useLimits, setUseLimits] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AdvAuditResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    if (!sku.trim()) { setErr('Укажите SKU товара.'); return }
    setBusy(true); setErr(null); setResult(null)
    try {
      const payload: AdvAuditPayload = { listing_id: listingId, marketplace, sku: sku.trim() }
      if (useLimits) {
        const c: Partial<AdvThresholds> = {}
        let complete = true
        for (const { key } of TH_FIELDS) {
          const v = num(th[key])
          if (v === undefined) complete = false
          else (c as Record<string, number>)[key] = v
        }
        if (complete) payload.thresholds = c as AdvThresholds
        else { setErr('Заполните все пороги площадки или отключите их.'); setBusy(false); return }
      }
      const res = await api.advertising.runAdvertisingAudit(payload)
      setResult(res)
      if (res.ok) onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Ошибка отправки')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 16 }}>
      <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginBottom: 12 }}>
        Аудит берёт цифры из импортированных финансов по SKU. Реклама оценивается по влиянию на прибыль, маржу и остатки.
      </div>
      <div><label style={lab}>SKU товара</label><input style={inp} value={sku} onChange={(e) => setSku(e.target.value)} /></div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0 4px', fontSize: 12, color: 'var(--text-2)' }}>
        <input type="checkbox" checked={useLimits} onChange={(e) => setUseLimits(e.target.checked)} />
        Указать пороги площадки (иначе часть рисков останется «не оценено»)
      </label>
      {useLimits && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 6 }}>
          {TH_FIELDS.map(({ key, label }) => (
            <div key={key}>
              <label style={lab}>{label}</label>
              <input style={inp} type="number" value={th[key] ?? ''} onChange={(e) => setTh({ ...th, [key]: e.target.value })} />
            </div>
          ))}
        </div>
      )}

      <button onClick={submit} disabled={busy} style={{ marginTop: 14, padding: '9px 16px', fontSize: 12.5, fontWeight: 600, borderRadius: 8, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--line)', background: 'var(--surface-h)', color: 'var(--text)', opacity: busy ? 0.6 : 1 }}>
        {busy ? 'Аудит…' : 'Проверить рекламу'}
      </button>

      {err && <div style={{ marginTop: 10, fontSize: 12, color: 'var(--danger)' }}>{err}</div>}
      {result && !result.ok && result.status === 'finance_unavailable' && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-3)' }}>
          Рекламный аудит пока недоступен: нет финансовых данных по этому товару.
        </div>
      )}
      {result && result.ok && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-2)' }}>
          Аудит выполнен. Проблем: {result.total_problems ?? 0}. Не удалось оценить: {result.total_not_evaluated ?? 0}.
        </div>
      )}
    </div>
  )
}

export default AdvertisingAuditForm

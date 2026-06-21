'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { LegalAuditResult } from '@/lib/api'

// MVP trigger: sku + marketplace → POST /api/legal/audit (subject_type=product).
// legal_unavailable is a calm note, NOT an error. No verdict, no promise.

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
  | { kind: 'done'; result: LegalAuditResult }
  | { kind: 'error'; message: string }

export function LegalAuditForm(
  { listingId, marketplace, sku, onDone }:
  { listingId?: string; marketplace?: string; sku?: string; onDone?: () => void },
) {
  const [skuV, setSkuV] = useState(sku ?? '')
  const [mp, setMp] = useState(marketplace ?? 'wildberries')
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit() {
    if (!skuV.trim() || busy) return
    setBusy(true); setOutcome({ kind: 'idle' })
    try {
      const result = await api.legalNavigator.runLegalAudit({
        marketplace: mp, subject_type: 'product', subject_ref: skuV.trim(),
        sku: skuV.trim(), listing_id: listingId,
      })
      if (result.status === 'legal_unavailable' || result.ok === false) {
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
      <div style={{ fontSize: 10.5, color: 'var(--text-3)' }}>
        Проверка показывает, что стоит проверить. Это рекомендация, а не юридическое заключение.
      </div>
      <button onClick={submit} disabled={busy || !skuV.trim()} style={{
        alignSelf: 'flex-start', padding: '8px 14px', fontSize: 12.5, fontWeight: 600,
        borderRadius: 8, border: '1px solid var(--line)',
        background: busy ? 'var(--surface)' : 'var(--surface-h)',
        color: 'var(--text)', cursor: busy || !skuV.trim() ? 'default' : 'pointer',
        opacity: busy || !skuV.trim() ? 0.6 : 1,
      }}>{busy ? 'Проверяем…' : 'Проверить юридические риски'}</button>

      {outcome.kind === 'unavailable' && (
        <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
          Недостаточно данных по этому товару для проверки.
        </div>
      )}
      {outcome.kind === 'done' && (
        <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
          Проверка выполнена. Отмечено к проверке: {outcome.result.total_findings ?? 0}.
        </div>
      )}
      {outcome.kind === 'error' && (
        <div style={{ fontSize: 12, color: 'var(--danger)' }}>Ошибка: {outcome.message}</div>
      )}
    </div>
  )
}

export default LegalAuditForm

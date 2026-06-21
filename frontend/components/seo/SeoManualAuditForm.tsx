'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { SeoAuditPayload, SeoManualConstraints, SeoAuditResult } from '@/lib/api'

/**
 * SeoManualAuditForm — отправка данных карточки на аудит вручную (manual mode).
 * MVP-поля. Лимиты площадки опциональны: если не заданы — соответствующие
 * проверки честно остаются «не оценено», лимиты не выдумываются.
 */
function num(v: string): number | undefined {
  const n = Number(v)
  return v.trim() === '' || Number.isNaN(n) ? undefined : n
}

const inp: React.CSSProperties = {
  width: '100%', padding: '8px 10px', fontSize: 12.5, borderRadius: 8,
  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text)',
}
const lab: React.CSSProperties = { fontSize: 11, color: 'var(--text-3)', marginBottom: 4, display: 'block' }

const CONS_FIELDS: { key: keyof SeoManualConstraints; label: string }[] = [
  { key: 'title_min_len', label: 'Заголовок мин.' },
  { key: 'title_max_len', label: 'Заголовок макс.' },
  { key: 'description_min_len', label: 'Описание мин.' },
  { key: 'media_min_images', label: 'Мин. фото' },
  { key: 'attribute_fill_rate_threshold', label: 'Полнота атрибутов (0–1)' },
  { key: 'content_completeness_threshold', label: 'Полнота карточки (0–1)' },
]

export function SeoManualAuditForm(
  { listingId, marketplace, onDone }: { listingId: string; marketplace: string; onDone: () => void },
) {
  const [f, setF] = useState<Record<string, string>>({})
  const [cons, setCons] = useState<Record<string, string>>({})
  const [useLimits, setUseLimits] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SeoAuditResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: e.target.value })

  async function submit() {
    setBusy(true); setErr(null); setResult(null)
    try {
      const snapshot: SeoAuditPayload['snapshot'] = {}
      if (f.sku) snapshot.sku = f.sku
      if (f.title !== undefined) snapshot.title = f.title
      if (f.description !== undefined) snapshot.description = f.description
      if (f.brand) snapshot.brand = f.brand
      if (f.image_count !== undefined && f.image_count !== '') snapshot.media = { image_count: Number(f.image_count) }
      if (useLimits) {
        const c: Partial<SeoManualConstraints> = {}
        let complete = true
        for (const { key } of CONS_FIELDS) {
          const v = num(cons[key])
          if (v === undefined) complete = false
          else (c as Record<string, number>)[key] = v
        }
        if (complete) snapshot.constraints = c as SeoManualConstraints
        else { setErr('Заполните все лимиты площадки или отключите их.'); setBusy(false); return }
      }
      const res = await api.seo.runSeoAudit({ listing_id: listingId, marketplace, snapshot })
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
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div><label style={lab}>SKU</label><input style={inp} value={f.sku ?? ''} onChange={set('sku')} /></div>
        <div><label style={lab}>Бренд</label><input style={inp} value={f.brand ?? ''} onChange={set('brand')} /></div>
        <div style={{ gridColumn: '1 / -1' }}><label style={lab}>Заголовок</label><input style={inp} value={f.title ?? ''} onChange={set('title')} /></div>
        <div style={{ gridColumn: '1 / -1' }}><label style={lab}>Описание</label><textarea style={{ ...inp, minHeight: 60 }} value={f.description ?? ''} onChange={set('description')} /></div>
        <div><label style={lab}>Кол-во фото</label><input style={inp} type="number" value={f.image_count ?? ''} onChange={set('image_count')} /></div>
      </div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0 4px', fontSize: 12, color: 'var(--text-2)' }}>
        <input type="checkbox" checked={useLimits} onChange={(e) => setUseLimits(e.target.checked)} />
        Указать лимиты площадки (иначе часть проверок останется «не оценено»)
      </label>
      {useLimits && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 6 }}>
          {CONS_FIELDS.map(({ key, label }) => (
            <div key={key}>
              <label style={lab}>{label}</label>
              <input style={inp} type="number" value={cons[key] ?? ''} onChange={(e) => setCons({ ...cons, [key]: e.target.value })} />
            </div>
          ))}
        </div>
      )}

      <button onClick={submit} disabled={busy} style={{
        marginTop: 14, padding: '9px 16px', fontSize: 12.5, fontWeight: 600, borderRadius: 8, cursor: busy ? 'default' : 'pointer',
        border: '1px solid var(--line)', background: 'var(--surface-h)', color: 'var(--text)', opacity: busy ? 0.6 : 1,
      }}>{busy ? 'Аудит…' : 'Проверить карточку'}</button>

      {err && <div style={{ marginTop: 10, fontSize: 12, color: 'var(--danger)' }}>{err}</div>}
      {result && !result.ok && result.status === 'snapshot_unavailable' && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-3)' }}>Не удалось собрать данные карточки для аудита.</div>
      )}
      {result && result.ok && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-2)' }}>
          Аудит выполнен. Найдено проблем: {result.total_problems ?? 0}. Не удалось оценить: {result.total_not_evaluated ?? 0}.
        </div>
      )}
    </div>
  )
}

export default SeoManualAuditForm

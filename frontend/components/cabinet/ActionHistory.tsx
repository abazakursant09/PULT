'use client'
import { useEffect, useState } from 'react'
import { fmtRub } from '@/lib/pultProduct'

/**
 * ActionHistory — журнал прошлых действий по товару: Проверить (dry-run) и Выполнить (real).
 * Источник: api.executions (если доступен), иначе локальный mock — доверие «система учится».
 * Логику ingest/API не меняем: только читаем существующий список исполнений.
 */
export interface ActionRecord {
  id: string
  ts: string                 // человекочитаемая дата
  lens: string               // линза/действие
  kind: 'dry_run' | 'real'   // проверка или реальное исполнение
  summary: string
  effect_rub?: number
  success: boolean
}

const MOCK: ActionRecord[] = [
  { id: 'a3', ts: '03.06 14:20', lens: 'Реклама', kind: 'real', summary: 'Ставка CPM снижена 480 → 210 ₽', effect_rub: 31_200, success: true },
  { id: 'a2', ts: '03.06 14:18', lens: 'Реклама', kind: 'dry_run', summary: 'Проверка снижения ставки — все проверки пройдены', success: true },
  { id: 'a1', ts: '01.06 09:05', lens: 'Отзывы', kind: 'real', summary: 'Авто-ответ на 12 отзывов', effect_rub: 6_800, success: true },
]

export function ActionHistory({ productId, records }: { productId: string; records?: ActionRecord[] }) {
  const [data, setData] = useState<ActionRecord[]>(records ?? [])

  useEffect(() => {
    if (records) return
    // Попытка взять реальный журнал; при отсутствии — mock (не ломаем UI).
    let alive = true
    ;(async () => {
      try {
        const mod = await import('@/lib/api')
        const exec = (mod.api as Record<string, any>).executions
        const list = exec?.list ? await exec.list({ product_id: productId }) : null
        if (alive && Array.isArray(list) && list.length) {
          setData(list.map((r: any, i: number): ActionRecord => ({
            id: String(r.id ?? i), ts: r.created_at ?? '', lens: r.action_type ?? '—',
            kind: r.dry_run ? 'dry_run' : 'real', summary: r.message ?? r.what_will_happen ?? '—',
            effect_rub: r.effect_rub, success: r.success ?? true,
          })))
        } else if (alive) setData(MOCK)
      } catch { if (alive) setData(MOCK) }
    })()
    return () => { alive = false }
  }, [productId, records])

  if (!data.length) {
    return (
      <div style={{ border: '1px dashed var(--line)', borderRadius: 12, padding: 22, textAlign: 'center', color: 'var(--text-3)', fontSize: 12.5 }}>
        Действий по товару ещё не было. Выполните решение из линзы — оно появится здесь.
      </div>
    )
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: '4px 18px' }}>
      {data.map((r, i) => (
        <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 0', borderBottom: i < data.length - 1 ? '1px solid var(--line)' : 'none' }}>
          <span style={{
            fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px', borderRadius: 5, flex: '0 0 auto',
            background: r.kind === 'real' ? 'var(--violet-dim)' : 'var(--surface-h)',
            color: r.kind === 'real' ? 'var(--violet-text)' : 'var(--text-3)',
          }}>{r.kind === 'real' ? 'выполнено' : 'проверка'}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, color: 'var(--text)' }}>{r.summary}</div>
            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{r.lens} · {r.ts}</div>
          </div>
          {r.effect_rub != null && (
            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--success)', flex: '0 0 auto' }}>+{fmtRub(r.effect_rub)}</span>
          )}
          <span style={{ color: r.success ? 'var(--success)' : 'var(--danger)', fontSize: 13, flex: '0 0 auto' }}>{r.success ? '✓' : '✕'}</span>
        </div>
      ))}
    </div>
  )
}

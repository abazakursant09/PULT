'use client'
import { useState } from 'react'
import InsightActionBlock from '@/components/InsightActionBlock'
import { LENS, fmtRub, type LensKey, type LensSignal, type ProductWithLenses } from '@/lib/pultProduct'
import { SEV } from './severity'

/**
 * SignalCard — АТОМ системы. Отвечает на 4 вопроса:
 * Что случилось? (Проблема) · Почему? (Причина) · Что делать? (Решение) · Что получу? (Эффект ₽).
 * Действие: «Проверить / Выполнить» через существующий InsightActionBlock
 * (dry_run vs execute), если у сигнала есть insightKey. Выполнить активно только в real.
 */
export function SignalCard({
  product, lens, signal, mode, dominant = false,
}: {
  product: ProductWithLenses; lens: LensKey; signal: LensSignal
  mode: 'real' | 'demo' | 'empty'; dominant?: boolean
}) {
  const [open, setOpen] = useState(dominant)
  const sev = SEV[signal.severity]
  const meta = LENS[lens]

  const badge =
    signal.badge === 'csv' ? { t: 'из CSV', c: 'var(--violet-text)', b: 'var(--violet-dim)' }
    : signal.badge === 'demo' || mode === 'demo' ? { t: 'demo', c: 'var(--warning)', b: 'var(--warning-dim)' }
    : { t: 'реальные', c: 'var(--success)', b: 'var(--success-dim)' }

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)',
      borderLeft: `4px solid ${sev.color}`, borderRadius: 14, padding: 18, marginBottom: 14,
    }}>
      {/* head: товар + линза */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 14 }}>
        <div style={thumb}>{product.photo}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--text)' }}>{product.name}</div>
          <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{product.mp} · {meta.icon} {meta.label}</div>
        </div>
        <span style={chip(sev.color, sev.dim)}>{sev.label}</span>
        <span style={{ ...chip(badge.c, badge.b), marginLeft: 7 }}>{badge.t}</span>
      </div>

      {/* formula: Проблема → Причина / Решение → Эффект */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1,
        background: 'var(--line)', border: '1px solid var(--line)', borderRadius: 11, overflow: 'hidden', marginBottom: 14,
      }}>
        <Cell k="⚠ Проблема" kc={sev.color} v={signal.problem} bold />
        <Cell k="↳ Причина" v={signal.cause} />
        <Cell k="✓ Решение" v={signal.solution} />
        <Cell k="↗ Эффект" kc="var(--success)"
          v={signal.effect_rub > 0 ? `+${fmtRub(signal.effect_rub)}/мес${signal.effect_text ? ` · ${signal.effect_text}` : ''}` : (signal.effect_text || '—')}
          vc="var(--success)" bold />
      </div>

      {/* actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {signal.insightKey ? (
          <button onClick={() => setOpen(v => !v)} style={btnGhost}>
            {open ? 'Скрыть' : 'Проверить / Выполнить'} {open ? '▴' : '▾'}
          </button>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{signal.solution}</span>
        )}
        <span style={{
          marginLeft: 'auto', fontSize: 10, fontWeight: 700, padding: '4px 10px', borderRadius: 20,
          background: signal.auto ? 'var(--violet-dim)' : 'var(--surface-h)',
          color: signal.auto ? 'var(--violet-text)' : 'var(--text-3)',
        }}>{signal.auto ? '⚡ авто' : 'ручное'}</span>
      </div>

      {/* InsightActionBlock — Проверить (dry-run) / Выполнить (execute) */}
      {open && signal.insightKey && (
        <div style={{ marginTop: 12 }}>
          <InsightActionBlock insightKey={signal.insightKey} />
        </div>
      )}
    </div>
  )
}

function Cell({ k, v, kc, vc, bold }: { k: string; v: string; kc?: string; vc?: string; bold?: boolean }) {
  return (
    <div style={{ background: 'var(--surface)', padding: '13px 15px' }}>
      <div style={{ fontSize: 9.5, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase', color: kc ?? 'var(--text-3)', marginBottom: 5 }}>{k}</div>
      <div style={{ fontSize: 13, lineHeight: 1.45, color: vc ?? 'var(--text)', fontWeight: bold ? 700 : 400 }}>{v}</div>
    </div>
  )
}

const thumb: React.CSSProperties = {
  width: 42, height: 42, borderRadius: 10, background: 'var(--surface-h)',
  border: '1px solid var(--line)', display: 'grid', placeItems: 'center', fontSize: 19, flex: '0 0 auto',
}
const chip = (c: string, b: string): React.CSSProperties => ({
  fontSize: 10, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase',
  padding: '4px 9px', borderRadius: 6, background: b, color: c,
})
const btnGhost: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, fontWeight: 600,
  padding: '8px 14px', borderRadius: 8, cursor: 'pointer',
  background: 'transparent', border: '1px solid var(--line)', color: 'var(--text-2)',
}

'use client'
import { useState } from 'react'
import InsightActionBlock from '@/components/InsightActionBlock'
import { LENS, fmtRub, type FlatSignal } from '@/lib/pultProduct'

/**
 * OpportunityCard — рост, не проблема. Показывает только потенциал: +X ₽/мес.
 * Формула та же (что/почему/что делать/что получу), акцент зелёный.
 */
export function OpportunityCard({ item, mode }: { item: FlatSignal; mode: 'real' | 'demo' | 'empty' }) {
  const [open, setOpen] = useState(false)
  const { product, lens, signal } = item
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)',
      borderLeft: '4px solid var(--success)', borderRadius: 14, padding: 18, marginBottom: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 10 }}>
        <div style={thumb}>{product.photo}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text)' }}>{product.name}</div>
          <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{LENS[lens].icon} {LENS[lens].label}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>упущено</div>
          <div style={{ fontSize: 19, fontWeight: 800, color: 'var(--success)' }}>+{fmtRub(signal.effect_rub)}/мес</div>
        </div>
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.45, marginBottom: 12 }}>
        <b style={{ color: 'var(--text)' }}>{signal.solution}.</b> {signal.cause}.{signal.effect_text ? ` ${signal.effect_text}.` : ''}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {signal.insightKey && (
          <button onClick={() => setOpen(v => !v)} style={btnGhost}>
            {open ? 'Скрыть' : 'Проверить / Выполнить'} {open ? '▴' : '▾'}
          </button>
        )}
        <span style={{
          marginLeft: 'auto', fontSize: 10, fontWeight: 700, padding: '4px 10px', borderRadius: 20,
          background: signal.auto ? 'var(--violet-dim)' : 'var(--surface-h)',
          color: signal.auto ? 'var(--violet-text)' : 'var(--text-3)',
        }}>{signal.auto ? '⚡ авто' : 'ручное'}</span>
      </div>
      {open && signal.insightKey && (
        <div style={{ marginTop: 12 }}><InsightActionBlock insightKey={signal.insightKey} /></div>
      )}
    </div>
  )
}

const thumb: React.CSSProperties = {
  width: 38, height: 38, borderRadius: 10, background: 'var(--surface-h)',
  border: '1px solid var(--line)', display: 'grid', placeItems: 'center', fontSize: 17, flex: '0 0 auto',
}
const btnGhost: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, fontWeight: 600,
  padding: '8px 14px', borderRadius: 8, cursor: 'pointer',
  background: 'transparent', border: '1px solid var(--line)', color: 'var(--text-2)',
}

'use client'
import Link from 'next/link'
import { LENS, type FlatSignal } from '@/lib/pultProduct'
import { SEV } from './severity'

/**
 * RiskCard — угроза бизнесу. Формула: Риск → Причина → Последствие → Действие.
 * Цель: селлер видит, какой ТОВАР может получить проблему.
 */
export function RiskCard({ item }: { item: FlatSignal }) {
  const { product, lens, signal } = item
  const sev = SEV[signal.severity]
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)',
      borderLeft: `4px solid ${sev.color}`, borderRadius: 14, padding: 18, marginBottom: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 14 }}>
        <div style={thumb}>{product.photo}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--text)' }}>{product.name}</div>
          <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{product.mp} · {LENS[lens].icon} {LENS[lens].label}</div>
        </div>
        <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', padding: '4px 9px', borderRadius: 6, background: sev.dim, color: sev.color }}>
          {signal.severity === 'red' ? 'высокий риск' : 'риск'}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--line)', border: '1px solid var(--line)', borderRadius: 11, overflow: 'hidden', marginBottom: 14 }}>
        <Cell k="⚠ Риск" kc={sev.color} v={signal.problem} bold />
        <Cell k="↳ Причина" v={signal.cause} />
        <Cell k="✕ Последствие" v={signal.effect_text || 'возможна блокировка/штраф'} />
        <Cell k="✓ Действие" kc="var(--violet-text)" v={signal.solution} />
      </div>

      <Link href={`/dashboard/products/${product.id}?lens=${lens}`} style={btnV}>{signal.solution} →</Link>
    </div>
  )
}

function Cell({ k, v, kc, bold }: { k: string; v: string; kc?: string; bold?: boolean }) {
  return (
    <div style={{ background: 'var(--surface)', padding: '13px 15px' }}>
      <div style={{ fontSize: 9.5, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase', color: kc ?? 'var(--text-3)', marginBottom: 5 }}>{k}</div>
      <div style={{ fontSize: 13, lineHeight: 1.45, color: 'var(--text)', fontWeight: bold ? 700 : 400 }}>{v}</div>
    </div>
  )
}

const thumb: React.CSSProperties = {
  width: 42, height: 42, borderRadius: 10, background: 'var(--surface-h)',
  border: '1px solid var(--line)', display: 'grid', placeItems: 'center', fontSize: 19, flex: '0 0 auto',
}
const btnV: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 700,
  padding: '10px 17px', borderRadius: 9, textDecoration: 'none', background: 'var(--violet)', color: '#fff',
}

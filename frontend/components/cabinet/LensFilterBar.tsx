'use client'
import { LENS, type LensKey } from '@/lib/pultProduct'

/**
 * LensFilterBar — линзы как фильтры ОДНОГО списка товаров (не отдельные роуты).
 * 'all' + по чипу на линзу со счётчиком.
 */
export function LensFilterBar({
  lenses, active, counts, onChange,
}: {
  lenses: LensKey[]; active: LensKey | 'all'; counts: Record<string, number>
  onChange: (l: LensKey | 'all') => void
}) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
      <Chip on={active === 'all'} onClick={() => onChange('all')} label="Все" />
      {lenses.map(k => (
        <Chip key={k} on={active === k} onClick={() => onChange(k)}
          label={`${LENS[k].icon} ${LENS[k].label}`} count={counts[k]} />
      ))}
    </div>
  )
}

function Chip({ on, onClick, label, count }: { on: boolean; onClick: () => void; label: string; count?: number }) {
  return (
    <button onClick={onClick} style={{
      fontSize: 12, padding: '7px 13px', borderRadius: 8, cursor: 'pointer', whiteSpace: 'nowrap',
      background: on ? 'var(--violet-dim)' : 'var(--surface-h)',
      border: `1px solid ${on ? 'var(--violet)' : 'var(--line)'}`,
      color: on ? 'var(--text)' : 'var(--text-2)',
    }}>
      {label}{count != null && <span style={{ fontSize: 10, marginLeft: 5, opacity: 0.7 }}>{count}</span>}
    </button>
  )
}

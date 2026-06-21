'use client'
import type { AdvProblem } from '@/lib/api'

/** AdvertisingProblemsList — найденные рекламные проблемы последнего аудита. */
const SEVERITY_RU: Record<string, string> = { critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий' }

function sevColor(s: string) {
  return s === 'critical' ? 'var(--danger)' : s === 'high' ? 'var(--text)' : 'var(--text-3)'
}

export function AdvertisingProblemsList({ problems }: { problems: AdvProblem[] }) {
  if (!problems.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '12px 0' }}>
        Рекламных проблем по товару не найдено.
      </div>
    )
  }
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: '2px 14px' }}>
      {problems.map((p, i) => (
        <div key={p.problem_type} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '11px 0', borderBottom: i < problems.length - 1 ? '1px solid var(--line)' : 'none' }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: sevColor(p.severity), flex: '0 0 auto', minWidth: 64 }}>{SEVERITY_RU[p.severity] ?? p.severity}</span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 12.5, color: 'var(--text)' }}>{p.problem_type}{p.category ? ` · ${p.category}` : ''}</div>
            {p.evidence && (
              <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                {Object.entries(p.evidence).map(([k, v]) => `${k}: ${String(v)}`).join(' · ')}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export default AdvertisingProblemsList

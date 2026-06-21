'use client'
import type { SeoSignal } from '@/lib/api'

/**
 * SeoSignalsList — язык PULT: что требует внимания, почему важно, что сделать,
 * ожидаемый эффект. Без технических терминов и без SEO Score.
 */
const PRIORITY_RU: Record<string, string> = {
  critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий',
}
const STATUS_RU: Record<string, string> = {
  active: 'Активен', reopened: 'Возобновлён', dismissed: 'Отклонён',
  resolved: 'Решён', promoted_to_decision: 'В работе',
}

function pri(level: string | null) {
  const crit = level === 'critical', high = level === 'high'
  return {
    label: PRIORITY_RU[level ?? ''] ?? '—',
    color: crit ? 'var(--danger)' : high ? 'var(--text)' : 'var(--text-3)',
    bg: crit ? 'var(--surface-h)' : 'var(--surface)',
  }
}

export function SeoSignalsList({ signals }: { signals: SeoSignal[] }) {
  if (!signals.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '14px 0' }}>
        Сейчас по карточке ничего не требует внимания.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {signals.map((s) => {
        const p = pri(s.priority_level)
        return (
          <div key={s.insight_key ?? s.signal_key} style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 14,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span style={{
                fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
                borderRadius: 5, background: p.bg, color: p.color, border: '1px solid var(--line)',
              }}>{p.label}</span>
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{STATUS_RU[s.status] ?? s.status}</span>
            </div>
            {s.what && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{s.what}</div>}
            {s.why && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему это важно:</b> {s.why}</div>}
            {s.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{s.meaning}</div>}
            {s.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что сделать:</b> {s.recommended_action}</div>}
            {s.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {s.expected_effect}</div>}
          </div>
        )
      })}
    </div>
  )
}

export default SeoSignalsList

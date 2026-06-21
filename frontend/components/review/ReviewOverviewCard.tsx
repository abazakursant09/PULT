'use client'
import type { ReviewOverview } from '@/lib/api'

// Reputation overview. No score — only honest counts. risk/attention/safe mirror
// the safety taxonomy; not_evaluated is shown separately (not "no problems").

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'risk' | 'warn' | 'ok' }) {
  const color =
    tone === 'risk' ? 'var(--danger)' : tone === 'warn' ? 'var(--text)' : tone === 'ok' ? 'var(--text)' : 'var(--text)'
  return (
    <div style={{ flex: 1, minWidth: 96 }}>
      <div style={{ fontSize: 10.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color }}>{value}</div>
    </div>
  )
}

export function ReviewOverviewCard({ overview }: { overview: ReviewOverview }) {
  const dt = overview.last_audit_at ? new Date(overview.last_audit_at).toLocaleString('ru-RU') : '—'
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <Stat label="Требуют реакции" value={overview.active_signals} tone={overview.active_signals ? 'warn' : undefined} />
        <Stat label="Риск для репутации" value={overview.risk_signals} tone={overview.risk_signals ? 'risk' : undefined} />
        <Stat label="Требуют внимания" value={overview.attention_signals} tone={overview.attention_signals ? 'warn' : undefined} />
        <Stat label="Можно ответить безопасно" value={overview.safe_signals} tone={overview.safe_signals ? 'ok' : undefined} />
        <Stat label="Открытых вопросов" value={overview.unresolved_problems} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, fontSize: 11.5, color: 'var(--text-3)', flexWrap: 'wrap', gap: 6 }}>
        <span>Последняя проверка: {dt}</span>
        {overview.total_not_evaluated > 0 && (
          <span>Часть отзыва не удалось оценить: {overview.total_not_evaluated}</span>
        )}
      </div>
    </div>
  )
}

export default ReviewOverviewCard

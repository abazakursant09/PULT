'use client'
import type { GrowthOverview } from '@/lib/api'

// Opportunity overview. No fabricated index, no prediction — honest live counts.

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'hi' | 'mid' }) {
  const color = tone === 'hi' ? 'var(--text)' : tone === 'mid' ? 'var(--text)' : 'var(--text)'
  return (
    <div style={{ flex: 1, minWidth: 96 }}>
      <div style={{ fontSize: 10.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color }}>{value}</div>
    </div>
  )
}

export function GrowthOverviewCard({ overview }: { overview: GrowthOverview }) {
  const dt = overview.last_audit_at ? new Date(overview.last_audit_at).toLocaleString('ru-RU') : '—'
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <Stat label="Возможности роста" value={overview.active_signals} tone={overview.active_signals ? 'hi' : undefined} />
        <Stat label="Высокий потенциал" value={overview.high_signals} tone={overview.high_signals ? 'hi' : undefined} />
        <Stat label="Средний потенциал" value={overview.medium_signals} tone={overview.medium_signals ? 'mid' : undefined} />
        <Stat label="Открытых возможностей" value={overview.unresolved_opportunities} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, fontSize: 11.5, color: 'var(--text-3)', flexWrap: 'wrap', gap: 6 }}>
        <span>Последняя проверка: {dt}</span>
        {overview.total_not_evaluated > 0 && (
          <span>Не удалось оценить часть возможностей: {overview.total_not_evaluated}</span>
        )}
      </div>
    </div>
  )
}

export default GrowthOverviewCard

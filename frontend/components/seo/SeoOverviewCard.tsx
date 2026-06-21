'use client'
import type { SeoOverview } from '@/lib/api'

/**
 * SeoOverviewCard — короткая сводка SEO по карточке. Без SEO Score —
 * только то, что требует внимания. `notEvaluated` показывает честно, что часть
 * карточки не удалось оценить (нет данных), а не «всё в порядке».
 */
function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'warn' | 'crit' }) {
  return (
    <div style={{ flex: 1, minWidth: 92 }}>
      <div style={{ fontSize: 10.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{
        fontSize: 20, fontWeight: 700, marginTop: 2,
        color: tone === 'crit' ? 'var(--danger)' : tone === 'warn' ? 'var(--text)' : 'var(--text)',
      }}>{value}</div>
    </div>
  )
}

export function SeoOverviewCard({ overview, notEvaluated }: { overview: SeoOverview; notEvaluated: number }) {
  const dt = overview.last_audit_at ? new Date(overview.last_audit_at).toLocaleString('ru-RU') : '—'
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <Stat label="Требуют внимания" value={overview.active_signals} tone={overview.active_signals ? 'warn' : undefined} />
        <Stat label="Критичных" value={overview.critical_signals} tone={overview.critical_signals ? 'crit' : undefined} />
        <Stat label="Высокий приоритет" value={overview.high_signals} tone={overview.high_signals ? 'warn' : undefined} />
        <Stat label="Открытых проблем" value={overview.unresolved_problems} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, fontSize: 11.5, color: 'var(--text-3)' }}>
        <span>Последний аудит: {dt}</span>
        {notEvaluated > 0 && <span>Не удалось оценить часть карточки: {notEvaluated}</span>}
      </div>
    </div>
  )
}

export default SeoOverviewCard

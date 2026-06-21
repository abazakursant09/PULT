'use client'
import type { LegalOverview, LegalSignal } from '@/lib/api'

// Advisory overview — no rating, no badge, no all-clear claim. Only honest status
// counts. not_evaluated is shown separately as "недостаточно данных".

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div style={{ flex: 1, minWidth: 90 }}>
      <div style={{ fontSize: 10.5, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: 'var(--text)' }}>{value}</div>
    </div>
  )
}

export function LegalOverviewCard(
  { overview, signals }: { overview: LegalOverview | null; signals: LegalSignal[] },
) {
  const by = (st: string) => signals.filter((s) => s.status === st).length
  const dt = overview?.last_audit_at ? new Date(overview.last_audit_at).toLocaleString('ru-RU') : '—'
  const notEval = overview?.total_not_evaluated ?? 0
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <Stat label="Требуют внимания" value={by('active') + by('reopened')} />
        <Stat label="Принято к сведению" value={by('acknowledged')} />
        <Stat label="Снято в проверке" value={by('resolved')} />
        <Stat label="Отклонено" value={by('dismissed')} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, fontSize: 11.5, color: 'var(--text-3)', flexWrap: 'wrap', gap: 6 }}>
        <span>Последняя проверка: {dt}</span>
        {notEval > 0 && <span>Недостаточно данных для проверки части требований: {notEval}</span>}
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 8 }}>
        Это рекомендации, а не юридическое заключение. При сомнениях стоит обратиться к специалисту.
      </div>
    </div>
  )
}

export default LegalOverviewCard

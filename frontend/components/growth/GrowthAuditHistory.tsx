'use client'
import type { GrowthAuditItem } from '@/lib/api'

const SEVERITY_RU: Record<string, string> = {
  critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий',
}
const TRIGGER_RU: Record<string, string> = {
  manual: 'Вручную', auto: 'Автоматически', scheduled: 'По расписанию',
}

export function GrowthAuditHistory({ audits }: { audits: GrowthAuditItem[] }) {
  if (!audits.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '14px 0' }}>
        Проверок возможностей роста ещё не было.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {audits.map((a) => {
        const dt = a.created_at ? new Date(a.created_at).toLocaleString('ru-RU') : '—'
        return (
          <div key={a.audit_id} style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10,
            padding: '10px 12px', display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', gap: 10, flexWrap: 'wrap',
          }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 12, color: 'var(--text)' }}>{dt}</span>
              <span style={{ fontSize: 10.5, color: 'var(--text-3)' }}>
                {TRIGGER_RU[a.triggered_by ?? ''] ?? a.triggered_by ?? '—'} · {a.status}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 14, fontSize: 11.5, color: 'var(--text-2)' }}>
              <span>Возможностей: <b>{a.total_problems}</b></span>
              <span>Не оценено: {a.total_not_evaluated}</span>
              {a.top_severity && <span>{SEVERITY_RU[a.top_severity] ?? a.top_severity}</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default GrowthAuditHistory

'use client'
import type { SeoAuditItem } from '@/lib/api'

/** SeoAuditHistory — история запусков аудита по карточке. */
const TRIGGER_RU: Record<string, string> = { manual: 'Вручную', scheduled: 'По расписанию', after_card_change: 'После изменения' }

export function SeoAuditHistory({ audits }: { audits: SeoAuditItem[] }) {
  if (!audits.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '12px 0' }}>
        Аудит ещё не запускался.
      </div>
    )
  }
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: '2px 14px' }}>
      {audits.map((a, i) => (
        <div key={a.audit_id} style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '11px 0', fontSize: 12,
          borderBottom: i < audits.length - 1 ? '1px solid var(--line)' : 'none',
        }}>
          <span style={{ color: 'var(--text-3)', flex: '0 0 auto', minWidth: 132 }}>
            {a.created_at ? new Date(a.created_at).toLocaleString('ru-RU') : '—'}
          </span>
          <span style={{ color: 'var(--text)', flex: 1 }}>
            Проблем: {a.total_problems} · Не оценено: {a.total_not_evaluated}
            {a.top_severity ? ` · приоритет: ${a.top_severity}` : ''}
          </span>
          <span style={{ color: 'var(--text-3)', flex: '0 0 auto' }}>{TRIGGER_RU[a.triggered_by ?? ''] ?? a.triggered_by ?? ''}</span>
        </div>
      ))}
    </div>
  )
}

export default SeoAuditHistory

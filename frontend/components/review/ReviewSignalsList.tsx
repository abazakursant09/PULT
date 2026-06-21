'use client'
import type { ReviewSignal } from '@/lib/api'

// Signal cards for the reputation contour. safety_mode is shown EXPLICITLY on
// every card. Safety copy is driven by safety_category — RISK forbids any
// auto-reply, ATTENTION needs manual approval, SAFE allows seller-enabled auto.
// This list never drafts, sends, or auto-publishes a reply.

const PRIORITY_RU: Record<string, string> = {
  critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий',
}
const STATUS_RU: Record<string, string> = {
  active: 'Требует реакции', reopened: 'Возобновлён', dismissed: 'Отклонён',
  resolved: 'Закрыт', promoted_to_decision: 'В работе',
}
const SAFETY_CAT_RU: Record<string, string> = {
  RISK: 'Риск для репутации', ATTENTION: 'Требует внимания', SAFE: 'Можно ответить безопасно',
}
const SAFETY_MODE_RU: Record<string, string> = {
  off: 'Отключено', manual_only: 'Только вручную', manual_approval: 'Ручное подтверждение',
  auto: 'Автоответ (включается вами вручную)',
}

// Per-category safety guidance shown to the seller. Mandated wording.
const SAFETY_COPY: Record<string, string> = {
  RISK: 'Автоответ запрещён. Ответ только вручную.',
  ATTENTION: 'Нужно ручное подтверждение.',
  SAFE: 'Можно отвечать вручную или настроить автоответ, если вы сами это включите.',
}

function pri(level: string | null) {
  const crit = level === 'critical', high = level === 'high'
  return {
    label: PRIORITY_RU[level ?? ''] ?? '—',
    color: crit ? 'var(--danger)' : high ? 'var(--text)' : 'var(--text-3)',
    bg: crit ? 'var(--surface-h)' : 'var(--surface)',
  }
}

function catColor(cat: string | null) {
  return cat === 'RISK' ? 'var(--danger)' : cat === 'ATTENTION' ? 'var(--text)' : 'var(--text-2)'
}

export function ReviewSignalsList({ signals }: { signals: ReviewSignal[] }) {
  if (!signals.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '14px 0' }}>
        Сейчас по отзывам ничего не требует реакции.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {signals.map((s) => {
        const p = pri(s.priority_level)
        const cat = s.safety_category ?? ''
        const safetyCopy = SAFETY_COPY[cat]
        return (
          <div key={s.insight_key ?? s.signal_key} style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 14,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
                borderRadius: 5, background: p.bg, color: p.color, border: '1px solid var(--line)',
              }}>{p.label}</span>
              {cat && (
                <span style={{ fontSize: 11, fontWeight: 600, color: catColor(s.safety_category) }}>
                  {SAFETY_CAT_RU[cat] ?? cat}
                </span>
              )}
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{STATUS_RU[s.status] ?? s.status}</span>
            </div>

            {s.what && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{s.what}</div>}
            {s.why && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему это важно:</b> {s.why}</div>}
            {s.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{s.meaning}</div>}
            {s.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что сделать:</b> {s.recommended_action}</div>}
            {s.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {s.expected_effect}</div>}

            {/* safety_mode is ALWAYS visible */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, paddingTop: 8,
              borderTop: '1px solid var(--line)', flexWrap: 'wrap',
            }}>
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>Режим ответа:</span>
              <span style={{
                fontSize: 10.5, fontWeight: 700, padding: '2px 7px', borderRadius: 5,
                border: '1px solid var(--line)',
                color: cat === 'RISK' ? 'var(--danger)' : 'var(--text-2)',
              }}>{SAFETY_MODE_RU[s.safety_mode ?? ''] ?? (s.safety_mode ?? '—')}</span>
            </div>
            {safetyCopy && (
              <div style={{
                fontSize: 11.5, marginTop: 6,
                color: cat === 'RISK' ? 'var(--danger)' : 'var(--text-3)',
              }}>{safetyCopy}</div>
            )}
            {s.review_id && (
              <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 6 }}>Отзыв: {s.review_id}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default ReviewSignalsList

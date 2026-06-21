'use client'
import type { GrowthSignal } from '@/lib/api'

// Opportunity cards. Shows the PULT doctrine (what/why/meaning/recommended_action/
// expected_effect) + category + confidence. Opportunity language only — no
// fabricated index, no prediction, no rival data, no promised growth.

const PRIORITY_RU: Record<string, string> = {
  critical: 'Критично', high: 'Высокий потенциал', medium: 'Средний потенциал', low: 'Низкий',
}
const STATUS_RU: Record<string, string> = {
  active: 'Возможность открыта', reopened: 'Возобновлена', dismissed: 'Отклонена',
  resolved: 'Закрыта', promoted_to_decision: 'В работе',
}
const CATEGORY_RU: Record<string, string> = {
  pricing: 'Цена', advertising: 'Реклама', seo: 'SEO', inventory: 'Остатки', reputation: 'Репутация',
}

function pri(level: string | null) {
  const hi = level === 'high' || level === 'critical'
  return {
    label: PRIORITY_RU[level ?? ''] ?? '—',
    color: hi ? 'var(--text)' : 'var(--text-3)',
    bg: hi ? 'var(--surface-h)' : 'var(--surface)',
  }
}

export function GrowthSignalsList({ signals }: { signals: GrowthSignal[] }) {
  if (!signals.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '14px 0' }}>
        Сейчас новых возможностей роста не найдено.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {signals.map((s) => {
        const p = pri(s.priority_level)
        const conf = s.confidence != null ? `${Math.round(s.confidence * 100)}%` : null
        return (
          <div key={s.insight_key ?? s.signal_key} style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 14,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
                borderRadius: 5, background: p.bg, color: p.color, border: '1px solid var(--line)',
              }}>{p.label}</span>
              {s.category && (
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-2)' }}>
                  {CATEGORY_RU[s.category] ?? s.category}
                </span>
              )}
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{STATUS_RU[s.status] ?? s.status}</span>
            </div>

            {s.what && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{s.what}</div>}
            {s.why && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему это важно:</b> {s.why}</div>}
            {s.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{s.meaning}</div>}
            {s.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что сделать:</b> {s.recommended_action}</div>}
            {s.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {s.expected_effect}</div>}

            <div style={{
              display: 'flex', alignItems: 'center', gap: 12, marginTop: 10, paddingTop: 8,
              borderTop: '1px solid var(--line)', fontSize: 11, color: 'var(--text-3)', flexWrap: 'wrap',
            }}>
              {s.effect_band && <span>Сила эффекта: {s.effect_band}</span>}
              {conf && <span>Уверенность: {conf}</span>}
              {s.recommended_action_key && <span>Действие: {s.recommended_action_key}</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default GrowthSignalsList

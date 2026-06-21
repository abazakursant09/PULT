'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { LegalSignal } from '@/lib/api'

// Advisory legal-risk cards: 5-part doctrine + cautious lifecycle. Never a verdict,
// never a law-breach claim, never an all-clear, never a promise. Lifecycle buttons
// call only the acknowledge/dismiss/reopen endpoints.

const STATUS_RU: Record<string, string> = {
  active: 'Требует внимания', reopened: 'Возобновлено', acknowledged: 'Принято к сведению',
  resolved: 'Снято в последней проверке', dismissed: 'Отклонено', promoted_to_decision: 'В работе',
}
const CATEGORY_RU: Record<string, string> = {
  certification: 'Сертификация', ip: 'Товарный знак', labeling: 'Маркировка', content: 'Контент и условия',
}
const RISK_RU: Record<string, string> = { high: 'высокий', medium: 'средний', low: 'низкий' }

const LIVE = new Set(['active', 'reopened', 'acknowledged'])

function btn(disabled: boolean): React.CSSProperties {
  return {
    fontSize: 11.5, padding: '5px 10px', borderRadius: 7, cursor: disabled ? 'default' : 'pointer',
    border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text-2)',
    opacity: disabled ? 0.5 : 1,
  }
}

export function LegalSignalsList(
  { signals, onChanged }: { signals: LegalSignal[]; onChanged?: () => void },
) {
  const [busy, setBusy] = useState<string | null>(null)

  async function act(id: string, action: 'acknowledge' | 'dismiss' | 'reopen') {
    if (busy) return
    setBusy(id + action)
    try {
      await api.legalNavigator[action](id)
      onChanged?.()
    } finally {
      setBusy(null)
    }
  }

  if (!signals.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '14px 0' }}>
        Сейчас потенциальных юридических рисков не отмечено. Это не значит, что рисков нет — данных может не хватать.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {signals.map((s) => {
        const live = LIVE.has(s.status)
        return (
          <div key={s.signal_id} style={{
            background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 14,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
                borderRadius: 5, background: 'var(--surface-h)', color: 'var(--text-2)', border: '1px solid var(--line)',
              }}>Потенциальный риск</span>
              {s.category && <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-2)' }}>{CATEGORY_RU[s.category] ?? s.category}</span>}
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{STATUS_RU[s.status] ?? s.status}</span>
              {s.risk_level && <span style={{ fontSize: 11, color: 'var(--text-3)' }}>риск: {RISK_RU[s.risk_level] ?? s.risk_level}</span>}
            </div>

            {s.what_happened && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{s.what_happened}</div>}
            {s.why_it_matters && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему важно:</b> {s.why_it_matters}</div>}
            {s.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{s.meaning}</div>}
            {s.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что стоит сделать:</b> {s.recommended_action}</div>}
            {s.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {s.expected_effect}</div>}
            {s.lifecycle_reason && (
              <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 6 }}>Статус: {s.lifecycle_reason}</div>
            )}

            <div style={{
              display: 'flex', gap: 6, marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--line)', flexWrap: 'wrap',
            }}>
              <button style={btn(!live || busy != null)} disabled={!live || busy != null}
                onClick={() => act(s.signal_id, 'acknowledge')}>Принять к сведению</button>
              <button style={btn(!live || busy != null)} disabled={!live || busy != null}
                onClick={() => act(s.signal_id, 'dismiss')}>Отклонить</button>
              <button style={btn(live || busy != null)} disabled={live || busy != null}
                onClick={() => act(s.signal_id, 'reopen')}>Вернуть в работу</button>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default LegalSignalsList

'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { DecisionFeedItem } from '@/lib/api'

// One feed item = one decision the seller can act on. No rating, no priority
// number, no prediction — cautious, observed/advisory text only.

const CONTOUR_RU: Record<string, string> = {
  seo: 'SEO', advertising: 'Реклама', review: 'Отзывы', growth: 'Рост',
  legal: 'Юридические риски', decision_outcome: 'Эффект решений',
}
const ATTENTION_RU: Record<string, string> = {
  new: 'Новое', seen: 'Просмотрено', snoozed: 'Отложено', acted: 'Выполнено', dismissed: 'Скрыто',
}
const EFFECT_RU: Record<string, string> = {
  proven_improved: 'Улучшение подтверждено наблюдением',
  proven_worsened: 'После решения метрика ухудшилась',
  proven_unchanged: 'Заметного изменения не зафиксировано',
  not_evaluated: 'Недостаточно данных, чтобы доказать эффект',
  not_measured_yet: 'Измерение ещё не закрыто',
}

function tomorrowISO(): string {
  return new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString()
}

type Action = 'seen' | 'snooze' | 'dismiss' | 'act'

export function DecisionFeedCard(
  { item, onChanged }: { item: DecisionFeedItem; onChanged: (itemKey: string, action: Action) => void },
) {
  const [busy, setBusy] = useState<Action | null>(null)

  async function run(action: Action) {
    if (busy) return
    setBusy(action)
    try {
      if (action === 'seen') await api.decisionFeed.markSeen(item.item_key)
      else if (action === 'snooze') await api.decisionFeed.snooze(item.item_key, tomorrowISO())
      else if (action === 'dismiss') await api.decisionFeed.dismiss(item.item_key)
      else await api.decisionFeed.markActed(item.item_key)
      onChanged(item.item_key, action)
    } finally {
      setBusy(null)
    }
  }

  const ctx = [CONTOUR_RU[item.contour] ?? item.contour, item.marketplace, item.sku]
    .filter(Boolean).join(' · ')

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 14,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
          borderRadius: 5, background: 'var(--surface-h)', color: 'var(--text-2)', border: '1px solid var(--line)',
        }}>{ctx}</span>
        <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{ATTENTION_RU[item.attention_state] ?? item.attention_state}</span>
      </div>

      {item.title && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{item.title}</div>}
      {item.what_happened && item.what_happened !== item.title && (
        <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 4 }}>{item.what_happened}</div>
      )}
      {item.why_it_matters && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему важно:</b> {item.why_it_matters}</div>}
      {item.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{item.meaning}</div>}
      {item.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что сделать:</b> {item.recommended_action}</div>}
      {item.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {item.expected_effect}</div>}
      {item.effect_status && (
        <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>
          {EFFECT_RU[item.effect_status] ?? item.effect_status}
          {item.effect_band ? ` (${item.effect_band})` : ''}
        </div>
      )}
      {item.lifecycle_reason && (
        <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 6 }}>Статус: {item.lifecycle_reason}</div>
      )}

      <div style={{
        display: 'flex', gap: 6, marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--line)', flexWrap: 'wrap',
      }}>
        {([
          ['seen', 'Отметить просмотренным'],
          ['snooze', 'Отложить'],
          ['dismiss', 'Скрыть'],
          ['act', 'Отметить выполненным'],
        ] as [Action, string][]).map(([a, label]) => (
          <button key={a} onClick={() => run(a)} disabled={busy != null} style={{
            fontSize: 11.5, padding: '5px 10px', borderRadius: 7,
            cursor: busy ? 'default' : 'pointer', border: '1px solid var(--line)',
            background: 'var(--surface)', color: 'var(--text-2)', opacity: busy ? 0.5 : 1,
          }}>{label}</button>
        ))}
      </div>
    </div>
  )
}

export default DecisionFeedCard

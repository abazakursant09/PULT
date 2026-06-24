'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { DecisionFeedItem, DecisionApplyPreview, DecisionApplyConfirmResult } from '@/lib/api'

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

// cautious reason copy for the apply flow — no promises, no all-clear claims
const APPLY_REASON_RU: Record<string, string> = {
  payload_not_derivable: 'Недостаточно данных для применения',
  unsupported_capability: 'Маркетплейс не поддерживает это действие',
  not_bindable: 'Это решение нельзя применить через PULT',
  action_key_mismatch: 'Решение нельзя применить',
  safety_not_manual_approval: 'Требуется ручная проверка',
  rejected: 'Применение отклонено проверкой',
  idempotency_key_required: 'Не удалось подготовить применение',
}
function applyReason(r: string | null): string {
  return r ? (APPLY_REASON_RU[r] ?? r) : 'Решение пока нельзя применить'
}
function newIdempotencyKey(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  return c?.randomUUID ? c.randomUUID() : `apply-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function tomorrowISO(): string {
  return new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString()
}

type Action = 'seen' | 'snooze' | 'dismiss' | 'act'
type ApplyUI =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'preview'; p: DecisionApplyPreview }
  | { kind: 'done'; r: DecisionApplyConfirmResult }
  | { kind: 'error'; msg: string }

export function DecisionFeedCard(
  { item, onChanged }: { item: DecisionFeedItem; onChanged: (itemKey: string, action: Action) => void },
) {
  const [busy, setBusy] = useState<Action | null>(null)
  const [apply, setApply] = useState<ApplyUI>({ kind: 'idle' })

  // apply button shows ONLY for a promoted engine decision (decision_id present),
  // never for already-measured Decision Outcome effect items.
  const decisionId = (item.source_context?.decision_id as string | undefined) || undefined
  const showApply = !!decisionId && item.contour !== 'decision_outcome'

  async function onPreview() {
    if (!decisionId) return
    setApply({ kind: 'busy' })
    try {
      const p = await api.decisionApply.getPreview(decisionId, {
        marketplace: item.marketplace ?? '', sku: item.sku ?? undefined,
      })
      setApply({ kind: 'preview', p })
    } catch (e) {
      setApply({ kind: 'error', msg: e instanceof Error ? e.message : 'Ошибка' })
    }
  }

  async function onConfirm() {
    if (!decisionId) return
    setApply({ kind: 'busy' })
    try {
      const r = await api.decisionApply.confirm(decisionId, {
        marketplace: item.marketplace ?? '', sku: item.sku ?? undefined,
        idempotency_key: newIdempotencyKey(),
      })
      setApply({ kind: 'done', r })
    } catch (e) {
      setApply({ kind: 'error', msg: e instanceof Error ? e.message : 'Ошибка' })
    }
  }

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
      {/* Learning OS v3 — observed HISTORY for this marketplace (counts only),
          with an explicit "not a forecast" disclaimer. Never a prediction/score. */}
      {item.learning_context && (
        <div style={{
          marginTop: 8, padding: '7px 10px', borderRadius: 7,
          background: 'var(--surface-h)', border: '1px solid var(--line)',
        }}>
          <div style={{ fontSize: 12, color: 'var(--text-2)' }}>{item.learning_context}</div>
          {/* v5 — small "why this was shown" explanation (observed history, not forecast). */}
          <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 3, fontStyle: 'italic' }}>
            {item.learning_explain?.explanation_text ?? 'Это не прогноз, а только прошлые наблюдения.'}
          </div>
          {/* v6 — why this action ranks above its alternatives (observed only). */}
          {item.ranking_explain && (
            <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 5 }}>
              {item.ranking_explain.explanation_text}
            </div>
          )}
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

      {showApply && (
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--line)' }}>
          {apply.kind === 'idle' && (
            <button onClick={onPreview} style={{
              fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 7, cursor: 'pointer',
              border: '1px solid var(--line)', background: 'var(--surface-h)', color: 'var(--text)',
            }}>Применить решение</button>
          )}
          {apply.kind === 'busy' && (
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>Проверяем…</div>
          )}

          {apply.kind === 'preview' && !apply.p.applyable && (
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
              <b style={{ color: 'var(--text-2)' }}>Решение пока нельзя применить.</b>
              <div style={{ marginTop: 4 }}>{applyReason(apply.p.reason)}</div>
            </div>
          )}

          {apply.kind === 'preview' && apply.p.applyable && (
            <div style={{
              background: 'var(--surface-h)', border: '1px solid var(--line)', borderRadius: 8,
              padding: 12, fontSize: 12, color: 'var(--text-2)',
            }}>
              <div style={{ color: 'var(--text)', fontWeight: 600 }}>Можно применить · требуется подтверждение</div>
              <div style={{ marginTop: 6 }}>Будет отправлено действие: <b>{apply.p.action_key}</b></div>
              <div>Маркетплейс: {apply.p.marketplace} · SKU: {apply.p.sku}</div>
              {apply.p.payload && (
                <pre style={{
                  fontSize: 10.5, color: 'var(--text-3)', marginTop: 6, marginBottom: 0,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit',
                }}>{JSON.stringify(apply.p.payload, null, 0)}</pre>
              )}
              <div style={{ marginTop: 6, color: 'var(--text-3)' }}>
                Действие будет применено только после подтверждения.
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                <button onClick={onConfirm} style={{
                  fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 7, cursor: 'pointer',
                  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text)',
                }}>Подтвердить применение</button>
                <button onClick={() => setApply({ kind: 'idle' })} style={{
                  fontSize: 12, padding: '6px 12px', borderRadius: 7, cursor: 'pointer',
                  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text-3)',
                }}>Отмена</button>
              </div>
            </div>
          )}

          {apply.kind === 'done' && apply.r.ok && (
            <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
              Решение отправлено на применение. Статус: {apply.r.status}.
              {apply.r.measurement_opened && (
                <div style={{ marginTop: 4, color: 'var(--text-3)' }}>PULT начнёт измерять эффект.</div>
              )}
            </div>
          )}
          {apply.kind === 'done' && !apply.r.ok && (
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
              Решение не применено: {applyReason(apply.r.reason)}.
            </div>
          )}
          {apply.kind === 'error' && (
            <div style={{ fontSize: 12, color: 'var(--text-3)' }}>Не удалось выполнить: {apply.msg}</div>
          )}
        </div>
      )}
    </div>
  )
}

export default DecisionFeedCard

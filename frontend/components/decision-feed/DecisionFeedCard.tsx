'use client'
import { useState } from 'react'
import { Check, X, Minus, HelpCircle, Clock } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
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

// A2 — verdict mapping, effect_status ONLY. No calculation, no score, no ROL.
// tone drives neutral styling; not_evaluated and not_measured_yet stay neutral.
type Tone = 'good' | 'bad' | 'neutral'
const VERDICT: Record<string, { Icon: LucideIcon; label: string; tone: Tone }> = {
  proven_improved:  { Icon: Check,      label: 'Решение помогло',                          tone: 'good' },
  proven_worsened:  { Icon: X,          label: 'Решение ухудшило результат',               tone: 'bad' },
  proven_unchanged: { Icon: Minus,      label: 'Заметного изменения не зафиксировано',     tone: 'neutral' },
  not_evaluated:    { Icon: HelpCircle, label: 'Недостаточно данных для оценки результата.', tone: 'neutral' },
  not_measured_yet: { Icon: Clock,      label: 'Измерение ещё не завершено',               tone: 'neutral' },
}
// A3 — the prominent verdict badge shows only for these (a closed measurement).
const BADGE_STATUSES = new Set(['proven_improved', 'proven_worsened', 'proven_unchanged', 'not_evaluated'])

const TONE_STYLE: Record<Tone, { fg: string; bg: string; bd: string }> = {
  good:    { fg: '#15803d', bg: 'rgba(21,128,61,0.10)',  bd: 'rgba(21,128,61,0.35)' },
  bad:     { fg: '#b91c1c', bg: 'rgba(185,28,28,0.10)',  bd: 'rgba(185,28,28,0.35)' },
  neutral: { fg: 'var(--text-2)', bg: 'var(--surface-h)', bd: 'var(--line)' },
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

  // A2/A4 — verdict + measured state, derived from existing fields only.
  const verdict = item.effect_status ? VERDICT[item.effect_status] : undefined
  const tone = verdict ? TONE_STYLE[verdict.tone] : TONE_STYLE.neutral
  const isMeasured = item.contour === 'decision_outcome'
  const showBadge = !!item.effect_status && !!verdict && BADGE_STATUSES.has(item.effect_status)
  const VerdictIcon = verdict?.Icon

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12, padding: 14,
      // A4 — measured cards are visually distinct from active tasks (subtle, not
      // louder): toned left accent + muted surface. Ordering stays backend-driven.
      ...(isMeasured ? { borderLeft: `3px solid ${tone.bd}`, background: 'var(--surface-h)' } : {}),
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
          borderRadius: 5, background: 'var(--surface-h)', color: 'var(--text-2)', border: '1px solid var(--line)',
        }}>{ctx}</span>
        <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{ATTENTION_RU[item.attention_state] ?? item.attention_state}</span>
        {isMeasured && (
          <span style={{
            fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px',
            borderRadius: 5, background: 'var(--surface)', color: 'var(--text-3)', border: '1px solid var(--line)',
          }}>Измерено</span>
        )}
      </div>

      {item.title && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{item.title}</div>}
      {item.what_happened && item.what_happened !== item.title && (
        <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 4 }}>{item.what_happened}</div>
      )}
      {item.why_it_matters && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему важно:</b> {item.why_it_matters}</div>}
      {item.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{item.meaning}</div>}
      {item.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что сделать:</b> {item.recommended_action}</div>}
      {item.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {item.expected_effect}</div>}
      {/* A3 — prominent verdict badge for a closed measurement (proven_* / not_evaluated). */}
      {showBadge && VerdictIcon && verdict && (
        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 8,
          padding: '4px 9px', borderRadius: 7, fontSize: 12, fontWeight: 600,
          color: tone.fg, background: tone.bg, border: `1px solid ${tone.bd}`,
        }}>
          <VerdictIcon size={14} strokeWidth={2.5} />
          <span>{verdict.label}</span>
        </div>
      )}
      {/* A3 — measurement still open: quiet status line, no verdict badge. */}
      {item.effect_status === 'not_measured_yet' && VerdictIcon && verdict && (
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 6, fontSize: 11.5, color: 'var(--text-3)' }}>
          <VerdictIcon size={12} />
          <span>{verdict.label}</span>
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

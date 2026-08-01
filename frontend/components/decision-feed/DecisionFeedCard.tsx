'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { DecisionFeedItem, DecisionApplyPreview, DecisionApplyConfirmResult } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

// One feed item = one decision the seller can act on. No rating, no priority
// number, no prediction — cautious, observed/advisory text only.

const CONTOUR_RU: Record<string, string> = {
  seo: 'SEO', advertising: 'Реклама', review: 'Отзывы', growth: 'Рост',
  legal: 'Юридические риски', decision_outcome: 'Эффект решений',
  operations: 'Операции', pricing: 'Ценообразование',
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

// Honest localization of the OBSERVED reason codes already carried in
// source_context (reason/missing). No new reason is generated or interpreted —
// an unknown code falls back to the raw code, never invented text.
const REASON_RU: Record<string, string> = {
  no_finance_rows: 'Нет финансовых данных за период измерения',
  no_revenue: 'Нет выручки для расчёта ДРР',
  insufficient_data: 'Недостаточно наблюдаемых данных',
  baseline: 'Не получено значение до решения',
  after: 'Не получено значение после решения',
  no_metric: 'Для этого действия не определена метрика',
  no_observed_reader: 'Для этой метрики нет наблюдаемого источника',
  no_db: 'Нет доступа к данным измерения',
  no_scope: 'Не определён продавец для измерения',
  no_entity: 'Не определён товар для измерения',
}
// Only for the unmeasured statuses; measured effects never show a reason line.
const _UNMEASURED = new Set(['not_evaluated', 'not_measured_yet'])
function notEvaluatedReason(
  effectStatus: string | null | undefined,
  sc: Record<string, unknown> | null | undefined,
): string | null {
  if (!effectStatus || !_UNMEASURED.has(effectStatus) || !sc) return null
  const code = (sc.missing ?? sc.reason)   // missing has priority over reason
  if (typeof code !== 'string' || !code) return null
  return REASON_RU[code] ?? code           // unknown code → raw code, never invented
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
  { item, onChanged, roleLabel, hideProblem }: {
    item: DecisionFeedItem
    onChanged: (itemKey: string, action: Action) => void
    roleLabel?: string | null   // "Основной вариант" | "Альтернатива" — shown when grouped
    hideProblem?: boolean        // grouped variant: problem text is shown once in the group header
  },
) {
  const [busy, setBusy] = useState<Action | null>(null)
  const [apply, setApply] = useState<ApplyUI>({ kind: 'idle' })

  // Apply CTA shows for an engine signal that is either already promoted (decision_id
  // present) OR bound to an executor lever (action_key present) — never for measured
  // Decision Outcome effect items. Advice-only signals have no action_key → no CTA.
  const promotedId = (item.source_context?.decision_id as string | undefined) || undefined
  // decision_id resolved on demand by click-triggered promotion (below).
  const [resolvedId, setResolvedId] = useState<string | undefined>(undefined)
  const decisionId = promotedId || resolvedId
  const showApply = item.contour !== 'decision_outcome' && (!!promotedId || !!item.action_key)

  // Click-triggered promotion: if there is no Decision yet but the signal is bound, run
  // the EXISTING promote+bridge (owner-scoped, idempotent) and match this signal's
  // decision_id by canonical insight_key. Creates no marketplace action.
  async function ensureDecisionId(): Promise<string | undefined> {
    if (decisionId) return decisionId
    if (!item.action_key) return undefined
    const res = await api.promotionActivation.run({ contour: item.contour })
    const match = res.items.find((i) => i.insight_key === item.item_key && i.decision_id)
    const id = match?.decision_id ?? undefined
    if (id) setResolvedId(id)
    return id
  }

  async function onPreview() {
    setApply({ kind: 'busy' })
    try {
      const id = await ensureDecisionId()
      if (!id) {
        setApply({ kind: 'error', msg: 'Не удалось подготовить решение к применению' })
        return
      }
      const p = await api.decisionApply.getPreview(id, {
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
      // SECURITY-2D-1B-B: no client idempotency key — the executor derives it from Decision.id, so a
      // double-submit of the same decision cannot cause a second provider dispatch.
      const r = await api.decisionApply.confirm(decisionId, {
        marketplace: item.marketplace ?? '', sku: item.sku ?? undefined,
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
    <Card variant="surface" className="rounded-[12px] p-3.5">
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <Badge variant="neutral" className="text-[9.5px] uppercase rounded-[5px]">{ctx}</Badge>
        {roleLabel && (
          <Badge variant={item.action_role === 'primary' ? 'default' : 'neutral'} className="text-[9.5px] uppercase rounded-[5px]">
            {roleLabel}
          </Badge>
        )}
        <span className="text-[11px] text-[var(--text-3)]">{ATTENTION_RU[item.attention_state] ?? item.attention_state}</span>
      </div>

      {!hideProblem && item.title && <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text)' }}>{item.title}</div>}
      {!hideProblem && item.what_happened && item.what_happened !== item.title && (
        <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 4 }}>{item.what_happened}</div>
      )}
      {!hideProblem && item.why_it_matters && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}><b>Почему важно:</b> {item.why_it_matters}</div>}
      {!hideProblem && item.meaning && <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 2 }}>{item.meaning}</div>}
      {item.recommended_action && <div style={{ fontSize: 12.5, color: 'var(--text)', marginTop: 6 }}><b>Что сделать:</b> {item.recommended_action}</div>}
      {item.expected_effect && <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>Ожидаемый эффект: {item.expected_effect}</div>}
      {item.effect_status && (
        <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 4 }}>
          {EFFECT_RU[item.effect_status] ?? item.effect_status}
          {item.effect_band ? ` (${item.effect_band})` : ''}
        </div>
      )}
      {notEvaluatedReason(item.effect_status, item.source_context) && (
        <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
          Причина: {notEvaluatedReason(item.effect_status, item.source_context)}
        </div>
      )}
      {(() => {
        // Measure Quality — observed finance-data freshness for unmeasured effects.
        // Facts only: last data date, last import, rows in window, the window itself.
        // No verdict, no "outdated", no advice.
        const sc = item.source_context
        if (!sc || !item.effect_status || !_UNMEASURED.has(item.effect_status)) return null
        const f = sc.freshness as Record<string, unknown> | undefined
        if (!f) return null
        const lastDate = (f.last_finance_date as string | null) ?? null
        const lastImport = (f.last_import_at as string | null) ?? null
        const rows = typeof f.rows_in_window === 'number' ? f.rows_in_window : null
        const wStart = (f.window_start as string | null) ?? null
        const wEnd = (f.window_end as string | null) ?? null
        return (
          <div style={{
            marginTop: 6, padding: '6px 9px', borderRadius: 6,
            background: 'var(--surface-h)', border: '1px solid var(--line)',
            fontSize: 10.5, color: 'var(--text-3)',
          }}>
            <div style={{ fontWeight: 600, color: 'var(--text-2)' }}>Данные для измерения:</div>
            <div>последняя дата финансовых данных: {lastDate ?? 'нет данных'}</div>
            <div>последний импорт: {lastImport ? lastImport.replace('T', ' ').slice(0, 16) : 'нет данных'}</div>
            <div>строк в окне измерения: {rows ?? 'нет данных'}</div>
            {wStart && wEnd && <div>окно: {wStart} — {wEnd}</div>}
          </div>
        )
      })()}
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
      {/* Learning-driven primary: WHY this lever is shown first (observed history,
          not a promise). Only when there is no learning_context block above to avoid
          duplicating the same explanation. */}
      {!item.learning_context && item.action_role === 'primary' && item.ranking_explain && (
        <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 6, fontStyle: 'italic' }}>
          {item.ranking_explain.explanation_text}
        </div>
      )}
      {item.lifecycle_reason && (
        <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 6 }}>Статус: {item.lifecycle_reason}</div>
      )}

      <div className="flex gap-1.5 mt-2.5 pt-2 border-t border-[var(--line)] flex-wrap">
        {([
          ['seen', 'Отметить просмотренным'],
          ['snooze', 'Отложить'],
          ['dismiss', 'Скрыть'],
          ['act', 'Отметить выполненным'],
        ] as [Action, string][]).map(([a, label]) => (
          <Button key={a} variant="ghost" size="sm" onClick={() => run(a)} disabled={busy != null}
            className="text-[11.5px] text-[var(--text-2)]">
            {label}
          </Button>
        ))}
      </div>

      {showApply && (
        <div className="mt-2.5 pt-2 border-t border-[var(--line)]">
          {apply.kind === 'idle' && (
            // The one high-value action on the card — the only primary Button in the feed.
            <Button variant="primary" size="sm" onClick={onPreview}>Применить решение</Button>
          )}
          {apply.kind === 'busy' && (
            <div className="text-[12px] text-[var(--text-3)]">Проверяем…</div>
          )}

          {apply.kind === 'preview' && !apply.p.applyable && (
            <div className="text-[12px] text-[var(--text-3)]">
              <b className="text-[var(--text-2)]">Решение пока нельзя применить.</b>
              <div className="mt-1">{applyReason(apply.p.reason)}</div>
            </div>
          )}

          {apply.kind === 'preview' && apply.p.applyable && (
            <div className="bg-[var(--surface-h)] border border-[var(--line)] rounded-[8px] p-3 text-[12px] text-[var(--text-2)]">
              <div className="text-[var(--text)] font-semibold">Можно применить · требуется подтверждение</div>
              <div className="mt-1.5">Будет отправлено действие: <b>{apply.p.action_key}</b></div>
              <div>Маркетплейс: {apply.p.marketplace} · SKU: {apply.p.sku}</div>
              {apply.p.payload && (
                <pre className="text-[10.5px] text-[var(--text-3)] mt-1.5 mb-0 whitespace-pre-wrap break-words font-[inherit]">
                  {JSON.stringify(apply.p.payload, null, 0)}
                </pre>
              )}
              <div className="mt-1.5 text-[var(--text-3)]">
                Действие будет применено только после подтверждения.
              </div>
              <div className="flex gap-1.5 mt-2 flex-wrap">
                <Button variant="primary" size="sm" onClick={onConfirm}>Подтвердить применение</Button>
                <Button variant="ghost" size="sm" onClick={() => setApply({ kind: 'idle' })} className="text-[var(--text-3)]">Отмена</Button>
              </div>
            </div>
          )}

          {apply.kind === 'done' && apply.r.ok && (
            <div className="text-[12px] text-[var(--text-2)]">
              Решение отправлено на применение. Статус: {apply.r.status}.
              {apply.r.measurement_opened && (
                <div className="mt-1 text-[var(--text-3)]">PULT начнёт измерять эффект.</div>
              )}
            </div>
          )}
          {apply.kind === 'done' && !apply.r.ok && (
            <div className="text-[12px] text-[var(--text-3)]">
              Решение не применено: {applyReason(apply.r.reason)}.
            </div>
          )}
          {apply.kind === 'error' && (
            <div className="text-[12px] text-[var(--text-3)]">Не удалось выполнить: {apply.msg}</div>
          )}
        </div>
      )}
    </Card>
  )
}

export default DecisionFeedCard

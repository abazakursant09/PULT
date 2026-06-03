'use client'

/**
 * Insight Action Block (ME-6.1).
 *
 * Turns an insight into one-click execution. UI holds NO business logic and
 * generates NO text — everything (reason / action / what / effect /
 * automation_eligible / needs_input) is rendered from the backend descriptor
 * returned by POST /api/insights/{key}/execute.
 *
 *   Проверить  → dry_run:true  (no marketplace call, no execution_log)
 *   Выполнить  → dry_run:false (real action via the shared Executor)
 */
import { useCallback, useEffect, useState } from 'react'
import { api, type InsightExecuteResult } from '@/lib/api'
import { T } from '@/lib/tokens'
import { trackEvent } from '@/lib/events'

type Props = { insightKey: string }

const C = {
  surf: T.surf, line: T.line, text: T.text, text2: T.text2, text3: T.text3,
  v: T.v, vDim: T.vDim, ok: T.ok, okD: T.okD, warn: T.warn, red: T.red,
}

const FIELD: Record<string, { label: string; placeholder: string }> = {
  campaign_id:   { label: 'ID рекламной кампании', placeholder: 'напр. 1234567' },
  cpm_or_action: { label: 'Новая ставка CPM, ₽ (или оставьте пустым для паузы)', placeholder: 'напр. 210' },
  card:          { label: 'Новый SEO-заголовок карточки', placeholder: 'напр. Блендер PowerBlend 1200 Вт, 6 скоростей' },
  price:         { label: 'Цена, ₽', placeholder: 'напр. 1990' },
}

export default function InsightActionBlock({ insightKey }: Props) {
  const [plan, setPlan] = useState<InsightExecuteResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [check, setCheck] = useState<InsightExecuteResult | null>(null)
  const [done, setDone] = useState<InsightExecuteResult | null>(null)
  const [inputs, setInputs] = useState<Record<string, string>>({})

  const buildOverrides = useCallback((): Record<string, unknown> => {
    const o: Record<string, unknown> = {}
    if (inputs.campaign_id) o.campaign_id = Number(inputs.campaign_id)
    if (inputs.cpm_or_action) o.cpm = Number(inputs.cpm_or_action)
    else if (inputs.campaign_id) o.action = 'pause'
    if (inputs.price) o.price = Number(inputs.price)
    if (inputs.card) o.card = { title: inputs.card }
    return o
  }, [inputs])

  // Populate the descriptor on mount via a safe dry-run (no side effects).
  useEffect(() => {
    let alive = true
    api.actionEngine.executeInsight(insightKey, { dry_run: true })
      .then(r => { if (alive) setPlan(r) })
      .catch(() => { /* insight may be non-executable; block stays hidden */ })
    return () => { alive = false }
  }, [insightKey])

  const run = useCallback(async (dry: boolean) => {
    setBusy(true)
    try {
      const r = await api.actionEngine.executeInsight(insightKey, { dry_run: dry, overrides: buildOverrides() })
      trackEvent(dry ? 'insight_checked' : 'insight_executed', 'action_engine', insightKey, { status: r.status })
      if (dry) { setCheck(r); setPlan(r); setDone(null) }
      else { setDone(r); setCheck(null); setPlan(r) }
    } catch {
      setCheck(null)
    } finally {
      setBusy(false)
    }
  }, [insightKey, buildOverrides])

  if (!plan) return null
  if (plan.action_type == null && plan.needs_input.includes('unsupported_insight')) return null

  const d = plan.descriptor || {}
  const needs = plan.needs_input.filter(k => k !== 'product')
  const blocked = needs.length > 0
  const card = (label: string, value?: string) => value ? (
    <div style={{ marginBottom: 6 }}>
      <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.08em', color: C.text3, textTransform: 'uppercase' }}>{label}</span>
      <p style={{ fontSize: 12.5, color: C.text, lineHeight: 1.45, marginTop: 2 }}>{value}</p>
    </div>
  ) : null

  return (
    <div style={{
      marginBottom: 14, padding: '12px 14px', borderRadius: 8,
      background: 'rgba(110,106,252,0.04)', border: `1px solid ${C.line}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.1em', color: C.v, textTransform: 'uppercase' }}>
          Действие
        </span>
        <AutomationBadge eligible={plan.automation_eligible} />
      </div>

      {card('Что будет сделано', d.what_will_happen)}
      {card('Ожидаемый эффект', d.expected_effect)}
      {!d.what_will_happen && card('Действие', d.action)}

      {/* needs_input form — UI shows it, backend decides it's needed */}
      {blocked && (
        <div style={{ margin: '8px 0', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {needs.map(k => {
            const f = FIELD[k] ?? { label: k, placeholder: '' }
            return (
              <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ fontSize: 10.5, color: C.text2 }}>{f.label}</span>
                <input
                  value={inputs[k] ?? ''}
                  placeholder={f.placeholder}
                  onChange={e => setInputs(s => ({ ...s, [k]: e.target.value }))}
                  style={{
                    background: '#0E0E10', border: `1px solid ${C.line}`, borderRadius: 6,
                    padding: '6px 9px', color: C.text, fontSize: 12.5, outline: 'none',
                  }}
                />
              </label>
            )
          })}
        </div>
      )}

      {/* Buttons */}
      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        <button
          disabled={busy}
          onClick={() => run(true)}
          style={{
            flex: '0 0 auto', padding: '7px 14px', borderRadius: 6, cursor: busy ? 'wait' : 'pointer',
            background: 'transparent', border: `1px solid ${C.line}`, color: C.text2, fontSize: 12.5, fontWeight: 600,
          }}>
          Проверить
        </button>
        <button
          disabled={busy || (blocked && Object.keys(buildOverrides()).length === 0)}
          onClick={() => run(false)}
          style={{
            flex: '0 0 auto', padding: '7px 16px', borderRadius: 6,
            cursor: busy ? 'wait' : 'pointer',
            background: C.v, border: `1px solid ${C.v}`, color: '#fff', fontSize: 12.5, fontWeight: 700,
            opacity: (blocked && Object.keys(buildOverrides()).length === 0) ? 0.45 : 1,
          }}>
          Выполнить
        </button>
      </div>

      {/* Dry-run check result */}
      {check && (
        <div style={{ marginTop: 10, padding: '8px 11px', borderRadius: 6, background: 'rgba(255,255,255,0.02)', borderLeft: `2px solid ${C.warn}` }}>
          <p style={{ fontSize: 10, fontWeight: 700, color: C.warn, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>Проверка</p>
          <p style={{ fontSize: 12, color: C.text2, lineHeight: 1.5 }}>
            {check.status === 'dry_run_ok' ? 'Проверки пройдены. Действие готово к выполнению.'
              : check.status === 'rejected' ? `Заблокировано: ${check.message}`
              : check.status === 'needs_input' ? 'Нужны дополнительные данные (см. форму выше).'
              : check.message}
          </p>
        </div>
      )}

      {/* Real execution result feedback */}
      {done && (
        <div style={{
          marginTop: 10, padding: '10px 12px', borderRadius: 6,
          background: done.success ? C.okD : 'rgba(239,68,68,0.08)',
          borderLeft: `2px solid ${done.success ? C.ok : C.red}`,
        }}>
          <p style={{ fontSize: 12.5, fontWeight: 700, color: done.success ? C.ok : C.red, marginBottom: 3 }}>
            {done.success ? '✓ Выполнено' : '✕ Не выполнено'}
          </p>
          <p style={{ fontSize: 11.5, color: C.text2, lineHeight: 1.5 }}>
            {d.what_will_happen ?? done.message}
          </p>
          {done.execution_id && (
            <p style={{ fontSize: 10, color: C.text3, marginTop: 4 }}>execution_id: {done.execution_id}</p>
          )}
          {done.results?.length > 0 && (
            <p style={{ fontSize: 10.5, color: C.text3, marginTop: 4 }}>
              Обработано записей: {done.results.length}
            </p>
          )}
        </div>
      )}

      {/* Escalation placeholder — container only (logic in a future sprint) */}
      <EscalationPlaceholder />
    </div>
  )
}

function AutomationBadge({ eligible }: { eligible: boolean }) {
  const txt = eligible ? 'Автоматизация доступна' : 'Требуется ручное выполнение'
  const col = eligible ? C.v : C.text3
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 20,
      background: eligible ? C.vDim : 'rgba(255,255,255,0.04)', color: col,
    }}>{txt}</span>
  )
}

function EscalationPlaceholder() {
  // ME-Escalation (future): Needs Review / Escalated / Waiting For Operator.
  // Rendered only when a backend escalation field exists — hidden for now.
  return null
}

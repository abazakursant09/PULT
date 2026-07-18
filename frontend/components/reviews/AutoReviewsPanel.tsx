'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, type MarketplaceConnectionOut, type AutomationRuleOut } from '@/lib/api'
import { parseUtc, formatSmart } from '@/lib/datetime'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

// AR-CONTROL-UI: seller manages Auto Reviews PER marketplace connection. Backend is the single
// source of truth and enforces consent + connection ownership; this panel only calls the AR-CONTROL
// endpoints and re-reads the result. Default OFF, explicit consent before the first enable, and an
// honest "not yet available" for marketplaces PULT has not wired (Yandex today).

const _MP_NAME: Record<string, string> = {
  wb: 'Wildberries', wildberries: 'Wildberries', ozon: 'Ozon',
  yandex: 'Яндекс Маркет', yandex_market: 'Яндекс Маркет', megamarket: 'Megamarket',
}
const _mp = (m: string) => _MP_NAME[m] || m

// Marketplaces where Auto Reviews is actually wired end-to-end (mirrors the backend review
// providers). Others are shown honestly as not-yet-available; the seller cannot consent/enable them.
const AR_SUPPORTED = new Set(['wildberries', 'ozon'])

const CONSENT_TEXT =
  'Разрешаю PULT обрабатывать отзывы и публиковать ответы в выбранном режиме для этого магазина. ' +
  'Автоматическая публикация применяется только к безопасным отзывам. ' +
  'Я могу отключить её в любой момент.'

function _errorText(e: unknown): string {
  const m = e instanceof Error ? e.message : String(e)
  if (m.includes('HTTP 401') || m.includes('HTTP 403')) return 'Нет доступа. Войдите в систему заново.'
  if (m.includes('NO_CONSENT')) return 'Сначала нужно дать согласие.'
  if (m.includes('KILL_SWITCH') || m.includes('disabled by the system'))
    return 'Автоматизация временно отключена системой. Попробуйте позже.'
  if (m.includes('MARKETPLACE_UNSUPPORTED')) return 'Auto Reviews для этого маркетплейса пока не поддерживаются.'
  if (m.includes('NO_FEEDBACKS')) return 'У подключения нет доступа к отзывам (feedbacks).'
  if (m.includes('NOT_CONNECTED')) return 'Подключение не активно.'
  if (m.includes('not found') || m.includes('HTTP 404')) return 'Подключение или правило не найдено.'
  return 'Не удалось выполнить действие. Попробуйте позже.'
}

function _hasConsent(r: AutomationRuleOut | null | undefined): boolean {
  return !!(r && r.consent_at && !r.consent_revoked_at)
}

// ── AR-VIS-1: review-sync state ─────────────────────────────────────────────────────────────────
// The seller should not sit in silence wondering whether review fetching is alive. This turns the
// two cadence fields the scheduler writes into one honest line. What it must NEVER do is name a
// CAUSE: the sync error code is only written to the server log, never to the database, so
// "marketplace unavailable" would be a guess. We say the schedule, not the reason.
// Timestamp handling lives in lib/datetime (AR-VIS-2) — shared with the reviews page so the
// naive-UTC rule cannot drift between the two surfaces.

function _syncStatus(conn: MarketplaceConnectionOut, enabled: boolean): { text: string; detail?: string } {
  // Consent is checked by the caller — this line only ever renders for a consented rule, so it can
  // never present a schedule as active for a seller who has not agreed.
  if (!enabled) return { text: 'Автоматизация отзывов выключена' }

  const raw = conn.review_sync_next_at
  if (!raw) return { text: 'Ожидается первая синхронизация' }   // nothing has run yet — do not imply it has

  const at = parseUtc(raw)
  if (!at) return { text: 'Время следующей проверки уточняется' }
  if (at.getTime() <= Date.now()) return { text: 'Проверка отзывов ожидается в ближайшее время' }

  const fails = conn.review_sync_fail_count ?? 0
  if (fails > 0) {
    return {
      text: `Синхронизация временно приостановлена. Следующая попытка — ${formatSmart(at)}`,
      detail: `Сбоев подряд: ${fails}`,
    }
  }
  return { text: `Следующая проверка отзывов — ${formatSmart(at)}` }
}

function SyncStatusLine({ conn, enabled }: { conn: MarketplaceConnectionOut; enabled: boolean }) {
  const { text, detail } = _syncStatus(conn, enabled)
  return (
    <p className="text-[12px] mb-3" style={{ color: 'var(--text-3)' }}>
      {text}{detail ? ` ${detail}` : ''}
    </p>
  )
}

function ConnectionAutoCard({ conn, sysEnabled }: { conn: MarketplaceConnectionOut; sysEnabled: boolean }) {
  const supported = AR_SUPPORTED.has(conn.marketplace)
  const connected = conn.status === 'connected'
  const [rule, setRule] = useState<AutomationRuleOut | null | undefined>(supported && connected ? undefined : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirmRevoke, setConfirmRevoke] = useState(false)

  const load = useCallback(async () => {
    if (!supported || !connected) return
    setError('')
    try {
      setRule(await api.automation.ruleForConnection(conn.id))
    } catch (e) {
      setError(_errorText(e))
      setRule(null)
    }
  }, [conn.id, supported, connected])

  useEffect(() => { load() }, [load])

  // Every mutation goes through here: it re-reads the fresh state from the backend afterwards, so the
  // UI never shows a state the server did not confirm.
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try {
      await fn()
      await load()
    } catch (e) {
      setError(_errorText(e))
      await load().catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  const grant = () => run(async () => {
    let r = rule
    if (!r) r = await api.automation.createForConnection(conn.id)
    await api.automation.grantConsent(r.id)
  })

  const header = (
    <div className="flex items-center justify-between mb-2">
      <span className="font-semibold text-[14px]" style={{ color: 'var(--text)' }}>{_mp(conn.marketplace)}</span>
      <Badge variant={connected ? 'default' : 'secondary'} className="text-[11px] rounded-[6px]">
        {connected ? 'Подключено' : `Статус: ${conn.status}`}
      </Badge>
    </div>
  )

  // 1) Marketplace PULT has not wired yet (Yandex) — honest, not hidden.
  if (!supported) {
    return (
      <Card className="p-4">
        {header}
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
          Auto Reviews для {_mp(conn.marketplace)} ещё не подключены.
        </p>
      </Card>
    )
  }

  // 2) Supported marketplace but the connection is not active.
  if (!connected) {
    return (
      <Card className="p-4">
        {header}
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
          Подключение не активно — управление Auto Reviews недоступно.
        </p>
      </Card>
    )
  }

  const consented = _hasConsent(rule)
  const enabled = !!rule?.enabled
  const mode = rule?.mode ?? 'confirm'

  return (
    <Card className="p-4">
      {header}

      {rule === undefined ? (
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>Загрузка…</p>
      ) : !consented ? (
        // 3) Default OFF — no consent yet. Explicit consent required before the first enable.
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Badge variant="secondary" className="text-[11px] rounded-[6px]">Выключено</Badge>
            <span className="text-[12px]" style={{ color: 'var(--text-3)' }}>Автоответы не публикуются</span>
          </div>
          <p className="text-[12px] leading-relaxed mb-3" style={{ color: 'var(--text-2)' }}>{CONSENT_TEXT}</p>
          <Button size="sm" loading={busy} onClick={grant}>Разрешить и настроить</Button>
        </div>
      ) : (
        // 4) Consent granted — full control: mode, enable/disable, revoke.
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Badge variant={enabled ? 'default' : 'secondary'} className="text-[11px] rounded-[6px]">
              {enabled ? 'Включено' : 'Выключено'}
            </Badge>
            <span className="text-[12px]" style={{ color: 'var(--text-3)' }}>Согласие получено</span>
          </div>

          {/* AR-VIS-1: is review fetching alive, and when does it look again? */}
          <SyncStatusLine conn={conn} enabled={enabled} />

          {/* Kill switch honesty: when the system has automation off, auto cannot run — say so and
              block choosing/enabling auto, so the seller never sees a false "on". */}
          {!sysEnabled && (
            <div className="text-[12px] rounded-[7px] px-2.5 py-2 mb-3 bg-[var(--warning-dim)] text-[var(--warning)]">
              Автоматизация временно отключена системой. Автоматическая публикация недоступна; ручной режим работает.
            </div>
          )}

          <div className="mb-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide mb-1.5" style={{ color: 'var(--text-3)' }}>Режим</p>
            <label className="flex items-start gap-2 mb-1.5 cursor-pointer">
              <input type="radio" name={`mode-${conn.id}`} checked={mode === 'confirm'} disabled={busy}
                onChange={() => run(() => api.automation.setMode(rule!.id, 'confirm'))} />
              <span className="text-[12px]" style={{ color: 'var(--text)' }}>Публиковать после моего подтверждения</span>
            </label>
            <label className="flex items-start gap-2 cursor-pointer">
              <input type="radio" name={`mode-${conn.id}`} checked={mode === 'auto'} disabled={busy || !sysEnabled}
                onChange={() => run(() => api.automation.setMode(rule!.id, 'auto'))} />
              <span className="text-[12px]" style={{ color: !sysEnabled ? 'var(--text-3)' : 'var(--text)' }}>Автоматически публиковать безопасные ответы</span>
            </label>
            {mode === 'auto' && sysEnabled && (
              <p className="text-[11px] mt-1.5" style={{ color: 'var(--text-3)' }}>
                Автоматически публикуются только безопасные ответы. Негативные отзывы никогда не публикуются автоматически.
              </p>
            )}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {/* Enabling an auto rule is blocked while the kill switch is off (it could never run).
                Disabling is always allowed; confirm-mode enable is unaffected. */}
            <Button size="sm" variant={enabled ? 'outline' : 'default'} loading={busy}
              disabled={busy || (!enabled && mode === 'auto' && !sysEnabled)}
              onClick={() => run(() => api.automation.toggle(rule!.id))}>
              {enabled ? 'Выключить автоматизацию' : 'Включить автоматизацию'}
            </Button>
            {!confirmRevoke ? (
              <Button size="sm" variant="ghost" disabled={busy}
                onClick={() => setConfirmRevoke(true)}>Отозвать согласие</Button>
            ) : (
              <span className="flex items-center gap-2">
                <span className="text-[12px]" style={{ color: 'var(--text-2)' }}>Автоматизация будет выключена. Отозвать?</span>
                <Button size="sm" variant="destructive" loading={busy}
                  onClick={() => run(async () => { await api.automation.revokeConsent(rule!.id); setConfirmRevoke(false) })}>
                  Да, отозвать
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmRevoke(false)}>Отмена</Button>
              </span>
            )}
          </div>
        </div>
      )}

      {error && <p className="text-[12px] mt-2" style={{ color: 'var(--danger)' }}>{error}</p>}
    </Card>
  )
}

export function AutoReviewsPanel() {
  const [conns, setConns] = useState<MarketplaceConnectionOut[] | null>(null)
  // System kill switch. Default to false (fail-safe honest) until the backend answers, so the UI
  // never implies auto works before it is confirmed available.
  const [sysEnabled, setSysEnabled] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.connections.list().then(setConns).catch(e => { setError(_errorText(e)); setConns([]) })
    api.automation.availability()
      .then(a => setSysEnabled(!!a.automation_enabled))
      .catch(() => setSysEnabled(false))
  }, [])

  if (conns === null) {
    return <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>Загрузка подключений…</p>
  }
  if (error) {
    return <p className="text-[12px]" style={{ color: 'var(--danger)' }}>{error}</p>
  }
  if (conns.length === 0) {
    return (
      <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
        Нет подключённых магазинов. Подключите маркетплейс, чтобы настроить автоответы.
      </p>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {conns.map(c => <ConnectionAutoCard key={c.id} conn={c} sysEnabled={sysEnabled} />)}
    </div>
  )
}

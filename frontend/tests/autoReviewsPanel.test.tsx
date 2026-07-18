import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AutoReviewsPanel } from '@/components/reviews/AutoReviewsPanel'
import { api, type MarketplaceConnectionOut, type AutomationRuleOut } from '@/lib/api'

// AR-CONTROL-UI: the seller manages Auto Reviews per connection. Backend is the source of truth;
// these guards fail if the panel ever shows a state the server did not confirm, enables without
// consent, or hides an unsupported marketplace.

const conn = (o: Partial<MarketplaceConnectionOut> = {}): MarketplaceConnectionOut => ({
  id: 'c1', marketplace: 'wildberries', label: null, status: 'connected',
  verification_status: 'unverified', scopes: ['feedbacks'], created_at: '2026-01-01T00:00:00Z', ...o,
})

const consentedRule: AutomationRuleOut = {
  id: 'r1', contour: 'reputation', action_type: 'publish_review_response', trigger: {}, guard: {},
  mode: 'confirm', enabled: false, connection_id: 'c1',
  consent_at: '2026-01-02T00:00:00Z', consent_version: 'v1', consent_revoked_at: null,
}

describe('AutoReviewsPanel (AR-CONTROL-UI)', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('shows Yandex honestly as not-yet-available and never fetches a rule for it', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn({ id: 'y', marketplace: 'yandex_market' })])
    const ruleSpy = vi.spyOn(api.automation, 'ruleForConnection')
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/ещё не подключены/)).toBeInTheDocument()
    expect(ruleSpy).not.toHaveBeenCalled()
    expect(screen.queryByText(/Разрешить и настроить/)).toBeNull()
  })

  it('default OFF: a supported connection with no rule shows the consent gate, not an enable toggle', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    vi.spyOn(api.automation, 'ruleForConnection').mockResolvedValue(null)
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/Разрешить и настроить/)).toBeInTheDocument()
    expect(screen.getByText(/Выключено/)).toBeInTheDocument()
    expect(screen.queryByText(/Включить автоматизацию/)).toBeNull()
  })

  it('with consent granted and automation available, shows mode choice + enable control', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    vi.spyOn(api.automation, 'ruleForConnection').mockResolvedValue(consentedRule)
    vi.spyOn(api.automation, 'availability').mockResolvedValue({ automation_enabled: true })
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/Автоматически публиковать безопасные ответы/)).toBeInTheDocument()
    expect(screen.getByText(/Публиковать после моего подтверждения/)).toBeInTheDocument()
    expect(screen.getByText(/Включить автоматизацию/)).toBeInTheDocument()
    expect(screen.getByText(/Согласие получено/)).toBeInTheDocument()
    expect(screen.queryByText(/временно отключена системой/)).toBeNull()
  })

  it('when the system kill switch is off, shows the honest notice and disables auto', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    vi.spyOn(api.automation, 'ruleForConnection').mockResolvedValue(consentedRule)
    vi.spyOn(api.automation, 'availability').mockResolvedValue({ automation_enabled: false })
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/Автоматизация временно отключена системой/)).toBeInTheDocument()
    // the "auto" radio is disabled — the seller cannot choose a mode the worker can't run
    const autoRadio = screen.getByText(/Автоматически публиковать безопасные ответы/)
      .closest('label')!.querySelector('input')!
    expect(autoRadio).toBeDisabled()
  })

  it('an inactive connection exposes no Auto Reviews controls', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn({ status: 'invalid' })])
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/Подключение не активно/)).toBeInTheDocument()
    expect(screen.queryByText(/Разрешить и настроить/)).toBeNull()
  })

  it('with no connections, guides the seller to connect a store', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([])
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/Нет подключённых магазинов/)).toBeInTheDocument()
  })
})

// ── AR-VIS-1: review-sync state line ────────────────────────────────────────────────────────────
// The seller must see whether review fetching is alive and when it looks again — and must never be
// told WHY it paused, because the sync error code is not stored anywhere in the database.

/** A backend timestamp: naive UTC, no timezone suffix — exactly what FastAPI emits today. */
const utcStamp = (msFromNow: number): string =>
  new Date(Date.now() + msFromNow).toISOString().replace('Z', '')

const enabledRule: AutomationRuleOut = { ...consentedRule, enabled: true }

describe('AutoReviewsPanel — review sync state (AR-VIS-1)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.automation, 'availability').mockResolvedValue({ automation_enabled: true })
  })

  const mount = async (c: Partial<MarketplaceConnectionOut>, rule: AutomationRuleOut | null = enabledRule) => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn(c)])
    vi.spyOn(api.automation, 'ruleForConnection').mockResolvedValue(rule)
    render(<AutoReviewsPanel />)
  }

  it('a consented but disabled rule says automation is off, not that a check is scheduled', async () => {
    await mount({ review_sync_next_at: utcStamp(10 * 60_000) }, consentedRule)   // enabled: false
    expect(await screen.findByText(/Автоматизация отзывов выключена/)).toBeInTheDocument()
    expect(screen.queryByText(/Следующая проверка отзывов/)).toBeNull()
  })

  it('without consent no schedule is shown at all, even when the backend sent one', async () => {
    await mount({ review_sync_next_at: utcStamp(10 * 60_000), review_sync_fail_count: 2 }, null)
    expect(await screen.findByText(/Разрешить и настроить/)).toBeInTheDocument()
    expect(screen.queryByText(/Следующая проверка отзывов/)).toBeNull()
    expect(screen.queryByText(/приостановлена/)).toBeNull()
    expect(screen.queryByText(/Автоматизация отзывов выключена/)).toBeNull()
  })

  it('a connection that never synced says the first sync is pending, not that one already ran', async () => {
    await mount({ review_sync_next_at: null, review_sync_fail_count: 0 })
    expect(await screen.findByText(/Ожидается первая синхронизация/)).toBeInTheDocument()
  })

  it('missing cadence fields are treated exactly like null — no invented time', async () => {
    await mount({})                                       // fields absent from the response entirely
    expect(await screen.findByText(/Ожидается первая синхронизация/)).toBeInTheDocument()
  })

  it('a healthy connection shows the next check time', async () => {
    await mount({ review_sync_next_at: utcStamp(10 * 60_000), review_sync_fail_count: 0 })
    expect(await screen.findByText(/Следующая проверка отзывов —/)).toBeInTheDocument()
    expect(screen.queryByText(/приостановлена/)).toBeNull()
  })

  it('a backed-off connection shows the pause, the retry time and the failure count', async () => {
    await mount({ review_sync_next_at: utcStamp(30 * 60_000), review_sync_fail_count: 3 })
    expect(await screen.findByText(/Синхронизация временно приостановлена/)).toBeInTheDocument()
    expect(screen.getByText(/Сбоев подряд: 3/)).toBeInTheDocument()
  })

  it('a past next_at is not presented as a future attempt', async () => {
    await mount({ review_sync_next_at: utcStamp(-5 * 60_000), review_sync_fail_count: 1 })
    expect(await screen.findByText(/в ближайшее время/)).toBeInTheDocument()
    expect(screen.queryByText(/Следующая попытка —/)).toBeNull()
  })

  it('a corrupt timestamp degrades to a neutral line instead of crashing', async () => {
    await mount({ review_sync_next_at: 'не-дата', review_sync_fail_count: 0 })
    expect(await screen.findByText(/Время следующей проверки уточняется/)).toBeInTheDocument()
  })

  it('never invents a reason for the pause — the cause is not stored anywhere', async () => {
    await mount({ review_sync_next_at: utcStamp(30 * 60_000), review_sync_fail_count: 5 })
    await screen.findByText(/Синхронизация временно приостановлена/)
    expect(document.body.textContent).not.toMatch(
      /429|401|403|5\d\d|таймаут|timeout|недоступ|rate limit|ошибка маркетплейс|лимит запросов/i,
    )
  })

  it('reads the suffix-less backend timestamp as UTC, not as local time', async () => {
    // 12:00 UTC. Parsed as local it would render the browser's 12:00 — i.e. shifted by the offset.
    const at = new Date(Date.now() + 3 * 3600_000)
    at.setUTCSeconds(0, 0)
    await mount({ review_sync_next_at: at.toISOString().replace('Z', ''), review_sync_fail_count: 0 })

    const expected = at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    expect(await screen.findByText(new RegExp(`Следующая проверка отзывов — .*${expected}`))).toBeInTheDocument()
  })

  it('an inactive connection shows no sync line (the connection branch wins)', async () => {
    await mount({ status: 'invalid', review_sync_next_at: utcStamp(10 * 60_000) })
    expect(await screen.findByText(/Подключение не активно/)).toBeInTheDocument()
    expect(screen.queryByText(/Следующая проверка отзывов/)).toBeNull()
  })

  it('an unsupported marketplace shows no sync line (the marketplace branch wins)', async () => {
    await mount({ marketplace: 'yandex_market', review_sync_next_at: utcStamp(10 * 60_000) })
    expect(await screen.findByText(/ещё не подключены/)).toBeInTheDocument()
    expect(screen.queryByText(/Следующая проверка отзывов/)).toBeNull()
  })
})

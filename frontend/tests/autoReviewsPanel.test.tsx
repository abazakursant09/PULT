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

  it('with consent granted, shows mode choice + enable control', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    vi.spyOn(api.automation, 'ruleForConnection').mockResolvedValue(consentedRule)
    render(<AutoReviewsPanel />)
    expect(await screen.findByText(/Автоматически публиковать безопасные ответы/)).toBeInTheDocument()
    expect(screen.getByText(/Публиковать после моего подтверждения/)).toBeInTheDocument()
    expect(screen.getByText(/Включить автоматизацию/)).toBeInTheDocument()
    expect(screen.getByText(/Согласие получено/)).toBeInTheDocument()
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

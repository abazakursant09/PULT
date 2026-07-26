import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConnectionsSection } from '@/components/connections/ConnectionsSection'
import { api, type MarketplaceConnectionOut } from '@/lib/api'

// CONNECTION-UI in Settings — read-only since 1.4.5I. Every NEW connection is created on the
// «Магазины» page, bound to a chosen cabinet. This section no longer offers an account-less create
// path: it shows status and the two safe actions that touch no key material (re-check, disconnect),
// and points the seller to «Магазины».

const conn = (o: Partial<MarketplaceConnectionOut> = {}): MarketplaceConnectionOut => ({
  id: 'c1', marketplace: 'wildberries', label: null, status: 'connected',
  verification_status: 'unverified', scopes: ['feedbacks'],
  scopes_verification: [{ scope: 'feedbacks', verification_status: 'unverified', verified_at: null }],
  created_at: '2026-01-01T00:00:00Z', ...o,
})

const verifyOut = (outcome: string) => ({
  connection_id: 'c1', marketplace: 'wildberries', scope: 'feedbacks', outcome,
  verification_status: outcome, verified_at: null,
  connection_verification_status: outcome, connection_verified_at: null,
  retry_after_seconds: null,
})

beforeEach(() => { vi.restoreAllMocks() })

describe('CONNECTION-UI — Settings is read-only, connect lives in «Магазины»', () => {
  it('sends the seller to «Магазины» and never offers an account-less connect here', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([])
    render(<ConnectionsSection />)
    expect(await screen.findByText(/управляются в разделе «Магазины»/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Перейти к магазинам/ })).toHaveAttribute('href', '/dashboard/stores')
    // no create/add path in this surface
    expect(screen.queryByRole('button', { name: /Подключить маркетплейс/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Подключить ещё/ })).toBeNull()
  })

  it('shows a proven-bad key as broken and warns that automation will not run', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn({
      scopes_verification: [{ scope: 'feedbacks', verification_status: 'invalid_credentials', verified_at: null }],
    })])
    render(<ConnectionsSection />)
    expect(await screen.findByText('Ключ не подошёл')).toBeInTheDocument()
    expect(screen.getByText(/Автоответы не будут работать/)).toBeInTheDocument()
  })

  it('re-checks a key on demand and reports the fresh verdict', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    const verify = vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('verified') as never)
    const user = userEvent.setup()
    render(<ConnectionsSection />)

    await user.click(await screen.findByRole('button', { name: /Повторить проверку/ }))
    await waitFor(() => expect(verify).toHaveBeenCalledWith('c1', 'feedbacks'))
    expect(await screen.findByText(/Ключ проверен/)).toBeInTheDocument()
  })

  it('disconnects only after an explicit confirmation, and says CSV is kept', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    const remove = vi.spyOn(api.connections, 'remove').mockResolvedValue(undefined as never)
    const user = userEvent.setup()
    render(<ConnectionsSection />)

    await user.click(await screen.findByRole('button', { name: 'Отключить' }))
    expect(remove).not.toHaveBeenCalled()
    expect(screen.getByText(/Кабинет, магазины, товары и загруженные CSV сохранятся/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Да, отключить/ }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('c1'))
  })

  it('a disconnected store shows no controls and points to «Магазины»', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn({ status: 'revoked' })])
    render(<ConnectionsSection />)

    expect(await screen.findByText('Отключён')).toBeInTheDocument()
    expect(screen.getByText(/Подключить его заново можно в разделе «Магазины»/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Повторить проверку/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Отключить' })).toBeNull()
  })

  it('a replaced key never keeps a stale green tick', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn({
      scopes_verification: [{ scope: 'feedbacks', verification_status: 'unverified', verified_at: null }],
    })])
    render(<ConnectionsSection />)
    expect(await screen.findByText('Ключ не проверен')).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/Ключ проверен/)
  })

  it('shows a translated message when the list cannot be loaded', async () => {
    vi.spyOn(api.connections, 'list').mockRejectedValue(new Error('HTTP 500'))
    render(<ConnectionsSection />)
    expect(await screen.findByText(/Не удалось выполнить действие/)).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/HTTP 500/)
  })
})

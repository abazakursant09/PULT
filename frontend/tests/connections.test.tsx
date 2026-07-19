import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConnectionsSection } from '@/components/connections/ConnectionsSection'
import { api, type MarketplaceConnectionOut } from '@/lib/api'

// CONNECTION-UI — the seller connects a marketplace here, and this is where the honesty is held.
//
// Two things must never happen: a key we have not checked (or have proven bad) must not read as
// working, and a raw server string must not reach the screen. The rest is plumbing.

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

const openDialog = async (existing: MarketplaceConnectionOut[] = []) => {
  vi.spyOn(api.connections, 'list').mockResolvedValue(existing)
  const user = userEvent.setup()
  render(<ConnectionsSection />)
  const label = existing.length ? /Подключить ещё/ : /Подключить маркетплейс/
  await user.click(await screen.findByRole('button', { name: label }))
  return user
}

describe('CONNECTION-UI — connecting a marketplace', () => {
  it('offers a way to connect when nothing is connected yet', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([])
    render(<ConnectionsSection />)
    expect(await screen.findByRole('button', { name: /Подключить маркетплейс/ })).toBeInTheDocument()
  })

  it('saves a Wildberries key and then actually verifies it', async () => {
    const create = vi.spyOn(api.connections, 'create').mockResolvedValue(conn())
    const verify = vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('verified') as never)
    const user = await openDialog()

    await user.type(screen.getByLabelText(/API-ключ Wildberries/), 'wb-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    await waitFor(() => expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ marketplace: 'wildberries', token: 'wb-key', scope: 'feedbacks' }),
    ))
    expect(verify).toHaveBeenCalledWith('c1', 'feedbacks')
    expect(await screen.findByText(/Ключ проверен/)).toBeInTheDocument()
  })

  it('does not send an Ozon connection without a Client-Id', async () => {
    const create = vi.spyOn(api.connections, 'create')
    const user = await openDialog()

    await user.click(screen.getByRole('button', { name: 'Ozon' }))
    await user.type(screen.getByLabelText(/API-ключ Ozon/), 'ozon-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByText('Для Ozon нужен Client-Id.')).toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })

  it('sends the Client-Id when Ozon is chosen', async () => {
    const create = vi.spyOn(api.connections, 'create').mockResolvedValue(conn({ marketplace: 'ozon' }))
    vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('verification_unsupported') as never)
    const user = await openDialog()

    await user.click(screen.getByRole('button', { name: 'Ozon' }))
    await user.type(screen.getByLabelText(/Client-Id/), 'CID')
    await user.type(screen.getByLabelText(/API-ключ Ozon/), 'ozon-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    await waitFor(() => expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ marketplace: 'ozon', ozon_client_id: 'CID' }),
    ))
  })

  it('says plainly when a key could not be checked — and never calls it verified', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue(conn({ marketplace: 'ozon' }))
    vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('verification_unsupported') as never)
    const user = await openDialog()

    await user.click(screen.getByRole('button', { name: 'Ozon' }))
    await user.type(screen.getByLabelText(/Client-Id/), 'CID')
    await user.type(screen.getByLabelText(/API-ключ Ozon/), 'k')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByText(/проверить его сейчас нельзя/)).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/Ключ проверен/)
  })

  it('reports a rejected key as not working', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue(conn())
    vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('invalid_credentials') as never)
    const user = await openDialog()

    await user.type(screen.getByLabelText(/API-ключ/), 'bad')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByText(/Ключ не подошёл/)).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/Ключ проверен/)
  })

  it('a timeout leaves the key saved and the check repeatable', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue(conn())
    vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('timeout') as never)
    const user = await openDialog()

    await user.type(screen.getByLabelText(/API-ключ/), 'k')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByText(/проверку можно повторить/)).toBeInTheDocument()
  })

  it('never shows the raw server error text', async () => {
    vi.spyOn(api.connections, 'create').mockRejectedValue(new Error('HTTP 422'))
    const user = await openDialog()

    await user.type(screen.getByLabelText(/API-ключ/), 'k')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByText('Проверьте заполненные поля.')).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/HTTP 422/)
  })

  it('keeps the secret out of the DOM and out of browser storage', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue(conn())
    vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('verified') as never)
    const setLocal = vi.spyOn(Storage.prototype, 'setItem')
    const user = await openDialog()

    await user.type(screen.getByLabelText(/API-ключ/), 'top-secret-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))
    await screen.findByText(/Ключ проверен/)

    expect(document.body.innerHTML).not.toContain('top-secret-key')
    expect(setLocal).not.toHaveBeenCalledWith(expect.anything(), expect.stringContaining('top-secret-key'))
  })

  it('masks the key field', async () => {
    await openDialog()
    expect(screen.getByLabelText(/API-ключ/)).toHaveAttribute('type', 'password')
  })

  it('lets a seller connect Yandex with just an API key — no cabinet id to type', async () => {
    const create = vi.spyOn(api.connections, 'create')
      .mockResolvedValue(conn({ marketplace: 'yandex' }))
    vi.spyOn(api.connections, 'verify').mockResolvedValue(verifyOut('verified') as never)
    const user = await openDialog()

    await user.click(screen.getByRole('button', { name: 'Яндекс Маркет' }))
    // Only one field: the businessId is discovered from the key itself, never typed.
    expect(screen.queryByLabelText(/Client-Id/)).toBeNull()
    await user.type(screen.getByLabelText(/API-ключ Яндекс Маркет/), 'ya-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    await waitFor(() => expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ marketplace: 'yandex', token: 'ya-key', scope: 'feedbacks' }),
    ))
  })

  it('tells the Yandex seller the key must be able to write, not only read', async () => {
    const user = await openDialog()
    await user.click(screen.getByRole('button', { name: 'Яндекс Маркет' }))
    expect(screen.getByText(/не только чтение/)).toBeInTheDocument()
  })
})

describe('CONNECTION-UI — managing an existing connection', () => {
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

  it('offers to replace the key', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    const user = userEvent.setup()
    render(<ConnectionsSection />)

    await user.click(await screen.findByRole('button', { name: /Заменить ключ/ }))
    expect(await screen.findByText('Подключить маркетплейс')).toBeInTheDocument()
  })

  it('disconnects only after an explicit confirmation', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn()])
    const remove = vi.spyOn(api.connections, 'remove').mockResolvedValue(undefined as never)
    const user = userEvent.setup()
    render(<ConnectionsSection />)

    await user.click(await screen.findByRole('button', { name: 'Отключить' }))
    expect(remove).not.toHaveBeenCalled()               // one click is not enough
    expect(screen.getByText(/Автоответы для этого магазина перестанут работать/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Да, отключить/ }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('c1'))
  })

  it('a disconnected store exposes no controls and says so', async () => {
    vi.spyOn(api.connections, 'list').mockResolvedValue([conn({ status: 'revoked' })])
    render(<ConnectionsSection />)

    expect(await screen.findByText('Отключён')).toBeInTheDocument()
    expect(screen.getByText(/Подключите его заново/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Повторить проверку/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Отключить' })).toBeNull()
  })

  it('a replaced key never keeps a stale green tick', async () => {
    // the backend resets that scope to `unverified` on replacement — the card must follow
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

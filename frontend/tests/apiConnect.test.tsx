import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AddCabinetDialog } from '@/components/stores/AddCabinetDialog'
import { ConnectApiDialog } from '@/components/stores/ConnectApiDialog'
import { api } from '@/lib/api'

// PULT-LAUNCH-1.4.5D — the seller connects an API key to a cabinet they already have.
//
// The rules under test are honesty rules: the marketplace is fixed by the cabinet, the key is
// bound to that cabinet, and "API проверен" is shown only after a real verify — never on save.

describe('AddCabinetDialog — API or CSV', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('offers both ways of getting data', () => {
    render(<AddCabinetDialog open onOpenChange={() => {}} onCreated={() => {}} />)
    expect(screen.getByRole('radio', { name: /Работать через CSV/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Подключить API/ })).toBeInTheDocument()
  })

  it('creating with API chosen hands the fresh cabinet to the connect step', async () => {
    const account = { id: 'acc-1', marketplace: 'wildberries', label: 'К', identity_status: 'unverified',
                      external_account_id: null, has_connection: false, stores: [] }
    vi.spyOn(api.marketplaceAccounts, 'create').mockResolvedValue(account as never)
    const onConnectApi = vi.fn()
    const user = userEvent.setup()
    render(<AddCabinetDialog open onOpenChange={() => {}} onCreated={() => {}} onConnectApi={onConnectApi} />)

    await user.type(screen.getByLabelText('Название кабинета'), 'Мой кабинет')
    await user.click(screen.getByRole('radio', { name: /Подключить API/ }))
    await user.click(screen.getByRole('button', { name: 'Добавить кабинет' }))

    await waitFor(() => expect(onConnectApi).toHaveBeenCalledWith(account))
  })
})

describe('ConnectApiDialog', () => {
  const base = {
    open: true as const,
    onOpenChange: () => {},
    marketplaceAccountId: 'acc-1',
    accountLabel: 'Основной кабинет',
    onConnected: () => {},
  }

  beforeEach(() => { vi.restoreAllMocks() })

  it('sends the key bound to the chosen cabinet, and fixes the marketplace', async () => {
    const create = vi.spyOn(api.connections, 'create').mockResolvedValue({ id: 'c-1' } as never)
    vi.spyOn(api.connections, 'verify').mockResolvedValue({ outcome: 'verified' } as never)
    const user = userEvent.setup()
    render(<ConnectApiDialog {...base} marketplace="wildberries" />)

    // no marketplace toggle — it is fixed by the cabinet
    expect(screen.queryByRole('radio')).toBeNull()
    await user.type(screen.getByLabelText('API-ключ'), 'wb-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    expect(create.mock.calls[0][0]).toMatchObject({
      marketplace: 'wildberries', marketplace_account_id: 'acc-1', token: 'wb-key',
    })
  })

  it('shows "API проверен" only after a successful verify', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue({ id: 'c-1' } as never)
    let resolveVerify: (v: unknown) => void = () => {}
    vi.spyOn(api.connections, 'verify').mockReturnValue(
      new Promise(res => { resolveVerify = res }) as never)
    const user = userEvent.setup()
    render(<ConnectApiDialog {...base} marketplace="wildberries" />)

    await user.type(screen.getByLabelText('API-ключ'), 'wb-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    // mid-verify: saved, checking — NOT connected
    expect(await screen.findByText(/Ключ сохранён, проверяем/)).toBeInTheDocument()
    expect(screen.queryByText('API проверен')).toBeNull()

    resolveVerify({ outcome: 'verified' })
    expect(await screen.findByText('API проверен')).toBeInTheDocument()
  })

  it('reports a failed verify without ever claiming success', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue({ id: 'c-1' } as never)
    vi.spyOn(api.connections, 'verify').mockResolvedValue({ outcome: 'revoked' } as never)
    const user = userEvent.setup()
    render(<ConnectApiDialog {...base} marketplace="wildberries" />)

    await user.type(screen.getByLabelText('API-ключ'), 'bad-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/недействителен/)
    expect(screen.queryByText('API проверен')).toBeNull()
  })

  it('for Yandex, verify success says the stores still need mapping — not "synced"', async () => {
    vi.spyOn(api.connections, 'create').mockResolvedValue({ id: 'c-1' } as never)
    vi.spyOn(api.connections, 'verify').mockResolvedValue({ outcome: 'verified' } as never)
    const user = userEvent.setup()
    render(<ConnectApiDialog {...base} marketplace="yandex" />)

    await user.type(screen.getByLabelText('API-ключ'), 'ym-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    expect(await screen.findByText(/Требуется сопоставить магазины/)).toBeInTheDocument()
    expect(screen.queryByText(/Данные синхронизированы/)).toBeNull()
  })

  it('asks Ozon for its Client-Id and never shows the token back or a fingerprint', async () => {
    const create = vi.spyOn(api.connections, 'create').mockResolvedValue({ id: 'c-1' } as never)
    vi.spyOn(api.connections, 'verify').mockResolvedValue({ outcome: 'verified' } as never)
    const user = userEvent.setup()
    const { container } = render(<ConnectApiDialog {...base} marketplace="ozon" />)

    await user.type(screen.getByLabelText('Client-Id'), '12345')
    await user.type(screen.getByLabelText('API-ключ'), 'ozon-key')
    await user.click(screen.getByRole('button', { name: 'Подключить' }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    // the token input is a password field, and nothing echoes the key or an internal fingerprint
    expect((screen.queryByLabelText('API-ключ') as HTMLInputElement | null)?.type).not.toBe('text')
    const text = container.textContent ?? ''
    expect(text).not.toContain('ozon-key')
    expect(text).not.toMatch(/fingerprint/i)
    expect(text).not.toMatch(/acc-1/)
  })
})

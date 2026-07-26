import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StoresLedger } from '@/components/stores/StoresLedger'
import { api } from '@/lib/api'
import type { MarketplaceAccountOut } from '@/lib/api'

// Archiving takes the store out of the import path, so it asks first. Restoring only gives
// capability back, so it does not. The PATCH must not fire until the seller has confirmed.

function ledger(status: 'active' | 'archived'): MarketplaceAccountOut[] {
  return [{
    id: 'acc-1', marketplace: 'yandex', label: 'Кабинет продавца',
    identity_status: 'unverified', external_account_id: null, has_connection: false,
    stores: [{
      id: 'st-1', marketplace_account_id: 'acc-1', marketplace: 'yandex',
      label: 'Москва — FBS', status, placement_type: null, external_store_id: null, source: 'manual',
    }],
  }] as MarketplaceAccountOut[]
}

describe('archiving a store', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('does not touch the backend until the seller confirms', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(ledger('active'))
    const patch = vi.spyOn(api.marketplaceAccounts, 'setStoreStatus')
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Москва — FBS')

    await user.click(screen.getByRole('button', { name: /Действия с магазином/ }))
    await user.click(await screen.findByRole('menuitem', { name: 'Архивировать магазин' }))

    expect(await screen.findByText('Архивировать магазин?')).toBeInTheDocument()
    expect(patch).not.toHaveBeenCalled()
  })

  it('explains what survives, and archives only on confirmation', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(ledger('active'))
    const patch = vi.spyOn(api.marketplaceAccounts, 'setStoreStatus').mockResolvedValue({} as never)
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Москва — FBS')

    await user.click(screen.getByRole('button', { name: /Действия с магазином/ }))
    await user.click(await screen.findByRole('menuitem', { name: 'Архивировать магазин' }))
    expect(screen.getByText(/Товары и история загрузок сохранятся/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Архивировать' }))
    await waitFor(() => expect(patch).toHaveBeenCalledWith('st-1', 'archived'))
  })

  it('cancels without changing anything', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(ledger('active'))
    const patch = vi.spyOn(api.marketplaceAccounts, 'setStoreStatus')
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Москва — FBS')

    await user.click(screen.getByRole('button', { name: /Действия с магазином/ }))
    await user.click(await screen.findByRole('menuitem', { name: 'Архивировать магазин' }))
    await user.click(screen.getByRole('button', { name: 'Отмена' }))

    await waitFor(() => expect(screen.queryByText('Архивировать магазин?')).toBeNull())
    expect(patch).not.toHaveBeenCalled()
  })

  it('restores immediately — nothing is taken away, so nothing is confirmed', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(ledger('archived'))
    const patch = vi.spyOn(api.marketplaceAccounts, 'setStoreStatus').mockResolvedValue({} as never)
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Москва — FBS')

    await user.click(screen.getByRole('button', { name: 'Восстановить' }))
    await waitFor(() => expect(patch).toHaveBeenCalledWith('st-1', 'active'))
    expect(screen.queryByText('Архивировать магазин?')).toBeNull()
  })

  it('offers no upload for an archived store', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(ledger('archived'))
    render(<StoresLedger />)
    await screen.findByText('Москва — FBS')
    expect(screen.queryByRole('link', { name: 'Загрузить CSV' })).toBeNull()
  })
})

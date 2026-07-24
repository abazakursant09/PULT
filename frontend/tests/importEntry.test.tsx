import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ImportEntryPage from '@/app/dashboard/import/page'
import { api } from '@/lib/api'
import type { MarketplaceAccountOut } from '@/lib/api'

// /dashboard/import used to upload a file with only a marketplace — a request the backend has
// refused since 1.4.2, because a CSV must name the store it lands in. The route is now the step
// that was missing: choose the store. It has no file input and no upload call, and there is
// exactly ONE CSV flow in the product.

const ENTRY = readFileSync(join(__dirname, '..', 'app', 'dashboard', 'import', 'page.tsx'), 'utf-8')

function accounts(status: 'active' | 'archived' = 'active'): MarketplaceAccountOut[] {
  return [{
    id: 'acc-1', marketplace: 'yandex', label: 'Кабинет продавца',
    identity_status: 'unverified', external_account_id: null, has_connection: false,
    stores: [{
      id: 'st-1', marketplace_account_id: 'acc-1', marketplace: 'yandex',
      label: 'Москва — FBS', status, placement_type: null, external_store_id: null, source: 'manual',
    }],
  }] as MarketplaceAccountOut[]
}

describe('import entry — choosing a store is obligatory', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('asks for the store in so many words', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(accounts())
    render(<ImportEntryPage />)
    expect(await screen.findByText('Сначала выберите магазин, в который загружаете данные')).toBeInTheDocument()
  })

  it('never uploads anything itself', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(accounts())
    const upload = vi.spyOn(api.csvImport, 'upload')
    const { container } = render(<ImportEntryPage />)
    await screen.findByText('Москва — FBS')

    expect(container.querySelector('input[type="file"]')).toBeNull()
    expect(upload).not.toHaveBeenCalled()
    // and the source cannot grow one by accident
    expect(ENTRY).not.toMatch(/csvImport\.upload/)
  })

  it('sends the seller into the one real flow, carrying the store', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(accounts())
    render(<ImportEntryPage />)
    const link = await screen.findByRole('link', { name: 'Выбрать' })
    expect(link.getAttribute('href')).toBe('/dashboard/stores/st-1/import')
  })

  it('lists no archived store, and says why the list is empty', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue(accounts('archived'))
    render(<ImportEntryPage />)
    expect(await screen.findByText(/Все ваши магазины в архиве/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Выбрать' })).toBeNull()
  })

  it('points a seller with no store at the place that creates one', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([])
    render(<ImportEntryPage />)
    expect(await screen.findByText(/Нет ни одного активного магазина/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Перейти к магазинам' }).getAttribute('href'))
      .toBe('/dashboard/stores')
  })

  it('reports a failed load', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockRejectedValue(new Error('HTTP 500'))
    render(<ImportEntryPage />)
    await waitFor(() => expect(screen.getByText(/Не удалось загрузить магазины/)).toBeInTheDocument())
  })
})

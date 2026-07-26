import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { YandexCampaignMapping } from '@/components/stores/YandexCampaignMapping'
import { api, type CampaignOut, type MarketplaceAccountOut, type MarketplaceStoreOut } from '@/lib/api'

// PULT-LAUNCH-1.4.5I §5 — after a Yandex key verifies, each campaign is bound to a PULT store,
// explicitly. Never auto-linked by name; no internal UUID shown; a new store is created only on ask.

const campaign = (o: Partial<CampaignOut> & { campaign_id: string }): CampaignOut => ({
  business_id: 'B1', label: null, placement_type: null, linked_store_id: null, link_state: 'unlinked', ...o,
})

const store = (o: Partial<MarketplaceStoreOut> & { id: string }): MarketplaceStoreOut => ({
  marketplace_account_id: 'a1', marketplace: 'yandex', label: 'Магазин', status: 'active',
  placement_type: null, external_store_id: null, source: 'manual', ...o,
})

function account(stores: MarketplaceStoreOut[]): MarketplaceAccountOut {
  return { id: 'a1', marketplace: 'yandex', label: 'Каб', identity_status: 'verified',
    external_account_id: 'B1', has_connection: true, stores }
}

beforeEach(() => { vi.restoreAllMocks() })

describe('YandexCampaignMapping', () => {
  it('lists campaigns by official id + label, and says an unlinked one is not yet mapped', async () => {
    vi.spyOn(api.connections, 'campaigns').mockResolvedValue([campaign({ campaign_id: '111', label: 'Основной' })])
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([account([])])
    render(<div className="ledger"><YandexCampaignMapping connectionId="c1" accountId="a1" /></div>)

    expect(await screen.findByText('Основной')).toBeInTheDocument()
    expect(screen.getByText(/ID магазина: 111/)).toBeInTheDocument()
    expect(screen.getByText(/ещё не связан с магазином PULT/)).toBeInTheDocument()
  })

  it('creates a new store on explicit action — never silently', async () => {
    vi.spyOn(api.connections, 'campaigns').mockResolvedValue([campaign({ campaign_id: '111', label: 'Основной' })])
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([account([])])
    const link = vi.spyOn(api.connections, 'linkCampaign')
      .mockResolvedValue({ campaign_id: '111', linked_store_id: 's-new', link_state: 'linked', created_store: true })
    const user = userEvent.setup()
    render(<div className="ledger"><YandexCampaignMapping connectionId="c1" accountId="a1" /></div>)

    await user.click(await screen.findByRole('button', { name: 'Создать новый магазин' }))
    await user.click(screen.getByRole('button', { name: 'Создать и связать' }))

    await waitFor(() => expect(link).toHaveBeenCalledWith('c1',
      expect.objectContaining({ campaign_id: '111', new_store_label: 'Основной' })))
  })

  it('links to an existing store by explicit selection', async () => {
    vi.spyOn(api.connections, 'campaigns').mockResolvedValue([campaign({ campaign_id: '111' })])
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([account([store({ id: 's1', label: 'Мой магазин' })])])
    const link = vi.spyOn(api.connections, 'linkCampaign')
      .mockResolvedValue({ campaign_id: '111', linked_store_id: 's1', link_state: 'linked', created_store: false })
    const user = userEvent.setup()
    render(<div className="ledger"><YandexCampaignMapping connectionId="c1" accountId="a1" /></div>)

    await user.click(await screen.findByRole('button', { name: 'Связать с магазином' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Магазин' }), 's1')
    await user.click(screen.getByRole('button', { name: 'Связать' }))

    await waitFor(() => expect(link).toHaveBeenCalledWith('c1', { campaign_id: '111', store_id: 's1' }))
  })

  it('a linked campaign shows its store and offers no create/link action', async () => {
    vi.spyOn(api.connections, 'campaigns').mockResolvedValue([
      campaign({ campaign_id: '111', link_state: 'linked', linked_store_id: 's1' })])
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([
      account([store({ id: 's1', label: 'Связанный', external_store_id: '111' })])])
    render(<div className="ledger"><YandexCampaignMapping connectionId="c1" accountId="a1" /></div>)

    expect(await screen.findByText(/Связан · Связанный/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Создать новый магазин' })).toBeNull()
  })
})

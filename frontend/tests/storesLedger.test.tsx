import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StoresLedger } from '@/components/stores/StoresLedger'
import { api } from '@/lib/api'
import type { MarketplaceAccountOut } from '@/lib/api'

// The ledger of cabinets and stores.
//
// Most of these tests are about isolation and honesty: the shape of a cabinet must follow the
// marketplace's real rule (WB/Ozon one store, Yandex many), and nothing PULT uses internally —
// store_key, uuids, external ids — may reach the screen.

function store(over: Partial<MarketplaceAccountOut['stores'][number]> = {}) {
  return {
    id: 'st-1', marketplace_account_id: 'acc-1', marketplace: 'wildberries',
    label: 'Основной магазин', status: 'active', placement_type: null,
    external_store_id: null, source: 'manual', ...over,
  }
}

function account(over: Partial<MarketplaceAccountOut> = {}): MarketplaceAccountOut {
  return {
    id: 'acc-1', marketplace: 'wildberries', label: 'Основной кабинет',
    identity_status: 'unverified', external_account_id: null, has_connection: false,
    stores: [store()], ...over,
  } as MarketplaceAccountOut
}

const WB = account()
const YANDEX = account({
  id: 'acc-2', marketplace: 'yandex', label: 'Кабинет продавца', has_connection: false,
  stores: [
    store({ id: 'st-2', marketplace_account_id: 'acc-2', marketplace: 'yandex', label: 'Москва — FBS' }),
    store({ id: 'st-3', marketplace_account_id: 'acc-2', marketplace: 'yandex', label: 'Казань — FBY' }),
    store({ id: 'st-4', marketplace_account_id: 'acc-2', marketplace: 'yandex', label: 'Ростов — DBS', status: 'archived' }),
  ],
})

describe('stores ledger', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('shows an empty state with a single way forward', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([])
    render(<StoresLedger />)
    expect(await screen.findByText(/Здесь появятся ваши магазины/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Добавить кабинет' })).toHaveLength(1)
  })

  it('asks the backend for the stores, not just the cabinets', async () => {
    const list = vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB])
    render(<StoresLedger />)
    await waitFor(() => expect(list).toHaveBeenCalledWith(true))
  })

  it('renders a WB cabinet as ONE line and offers no second store', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB])
    render(<StoresLedger />)
    expect(await screen.findByText('Основной кабинет')).toBeInTheDocument()
    expect(screen.getByText('Основной магазин')).toBeInTheDocument()
    // Wildberries has exactly one store per cabinet — offering to add another would be a lie.
    expect(screen.queryByRole('button', { name: /Добавить магазин в кабинет/ })).toBeNull()
  })

  it('renders a Yandex cabinet as a group that CAN take more stores', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([YANDEX])
    render(<StoresLedger />)
    expect(await screen.findByText('Кабинет продавца')).toBeInTheDocument()
    expect(screen.getByText('Москва — FBS')).toBeInTheDocument()
    expect(screen.getByText('Казань — FBY')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Добавить магазин в кабинет/ })).toBeInTheDocument()
  })

  it('shows the API status only when the backend reports a connection', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB, account({ id: 'acc-9', label: 'С ключом', has_connection: true, stores: [store({ id: 'st-9', marketplace_account_id: 'acc-9' })] })])
    render(<StoresLedger />)
    await screen.findByText('Основной кабинет')
    expect(screen.getAllByText('— API подключён')).toHaveLength(1)
  })

  it('searches by cabinet and by store name', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB, YANDEX])
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Основной кабинет')

    await user.type(screen.getByPlaceholderText(/Поиск по кабинету или магазину/), 'Казань')
    await waitFor(() => expect(screen.queryByText('Основной кабинет')).toBeNull())
    expect(screen.getByText('Казань — FBY')).toBeInTheDocument()
  })

  it('filters by archived and active', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB, YANDEX])
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Кабинет продавца')

    await user.click(screen.getByRole('button', { name: 'Архив' }))
    await waitFor(() => expect(screen.queryByText('Москва — FBS')).toBeNull())
    expect(screen.getByText('Ростов — DBS')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Активные' }))
    await waitFor(() => expect(screen.queryByText('Ростов — DBS')).toBeNull())
    expect(screen.getByText('Москва — FBS')).toBeInTheDocument()
  })

  it('collapses a cabinet without losing its count', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([YANDEX])
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Москва — FBS')

    await user.click(screen.getByRole('button', { expanded: true }))
    await waitFor(() => expect(screen.queryByText('Москва — FBS')).toBeNull())
    expect(screen.getByText(/3 магазина/)).toBeInTheDocument()
  })

  it('says so when a search matches nothing', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB])
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Основной кабинет')

    await user.type(screen.getByPlaceholderText(/Поиск по кабинету или магазину/), 'зззз')
    expect(await screen.findByText(/Ничего не найдено/)).toBeInTheDocument()
  })

  it('reports a failed load instead of showing an empty ledger', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockRejectedValue(new Error('HTTP 500'))
    render(<StoresLedger />)
    expect(await screen.findByText(/Не удалось загрузить магазины/)).toBeInTheDocument()
  })

  it('never shows an internal identifier', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB, YANDEX])
    const { container } = render(<StoresLedger />)
    await screen.findByText('Основной кабинет')

    const text = container.textContent ?? ''
    expect(text).not.toMatch(/primary/i)
    expect(text).not.toMatch(/store_key/)
    expect(text).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i)
    expect(text).not.toMatch(/acc-\d|st-\d/)
  })

  it('creates a WB cabinet without asking for a store name', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([WB])
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Основной кабинет')

    await user.click(screen.getByRole('button', { name: 'Добавить кабинет' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Добавить кабинет' }))
    expect(dialog.getByLabelText('Название кабинета')).toBeInTheDocument()
    expect(dialog.queryByLabelText('Название магазина')).toBeNull()
    expect(dialog.getByText(/один магазин на кабинет/)).toBeInTheDocument()
  })

  it('adds a Yandex store through its own endpoint', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([YANDEX])
    const create = vi.spyOn(api.marketplaceAccounts, 'createStore')
      .mockResolvedValue(store({ id: 'st-5', marketplace: 'yandex', label: 'Уфа — FBY' }) as never)
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Кабинет продавца')

    await user.click(screen.getByRole('button', { name: /Добавить магазин в кабинет/ }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Добавить магазин в кабинет' }))
    await user.type(dialog.getByLabelText('Название магазина'), 'Уфа — FBY')
    await user.click(dialog.getByRole('button', { name: 'Добавить магазин' }))

    await waitFor(() => expect(create).toHaveBeenCalledWith('acc-2', { label: 'Уфа — FBY' }))
  })

  it('reports a failed Yandex store creation without inventing a reason', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([YANDEX])
    vi.spyOn(api.marketplaceAccounts, 'createStore').mockRejectedValue(new Error('HTTP 500'))
    const user = userEvent.setup()
    render(<StoresLedger />)
    await screen.findByText('Кабинет продавца')

    await user.click(screen.getByRole('button', { name: /Добавить магазин в кабинет/ }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Добавить магазин в кабинет' }))
    await user.type(dialog.getByLabelText('Название магазина'), 'Уфа')
    await user.click(dialog.getByRole('button', { name: 'Добавить магазин' }))

    // Yandex allows many stores, so "this cabinet already has one" can never be the reason here.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Не удалось добавить магазин. Повторите попытку.')
    expect(alert.textContent).not.toMatch(/уже создан магазин/)
  })
})

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SourcePolicySection } from '@/components/stores/SourcePolicySection'
import { api, type SourcePolicyMetric, type SourcePolicyOut } from '@/lib/api'

// PULT-LAUNCH-1.4.5I §6/§8 — the seller chooses, per metric, where a store's numbers come from.
// Absent policy ⇒ CSV; the API option is disabled when the API cannot honestly source the metric.

const metric = (o: Partial<SourcePolicyMetric> & { metric_type: string }): SourcePolicyMetric => ({
  preference: 'csv', api_supported: true, api_available: false, limitation: null, ...o,
})

const ALL_METRICS = [
  'catalog', 'card_content', 'price', 'stock', 'orders', 'returns',
  'revenue', 'marketplace_fees', 'logistics', 'penalties', 'deductions', 'cogs', 'ad_spend',
]

function policy(overrides: Record<string, Partial<SourcePolicyMetric>> = {}, marketplace = 'wildberries'): SourcePolicyOut {
  return {
    store_id: 's1',
    marketplace,
    metrics: ALL_METRICS.map(m => metric({
      metric_type: m,
      api_supported: !['cogs', 'ad_spend'].includes(m),
      limitation: ['cogs', 'ad_spend'].includes(m) ? 'manual_only_csv' : null,
      ...(overrides[m] ?? {}),
    })),
  }
}

beforeEach(() => { vi.restoreAllMocks() })

describe('SourcePolicySection', () => {
  it('shows the four plain-language groups and defaults every metric to CSV', async () => {
    vi.spyOn(api.sourcePolicy, 'get').mockResolvedValue(policy())
    render(<div className="ledger"><SourcePolicySection storeId="s1" marketplace="wildberries" /></div>)

    expect(await screen.findByRole('heading', { name: 'Каталог' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Операции' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Деньги' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Данные продавца' })).toBeInTheDocument()

    // Выручка row: CSV is the active choice by default
    const revenue = screen.getByRole('radiogroup', { name: 'Выручка' })
    expect(within(revenue).getByRole('radio', { name: 'Только CSV', checked: true })).toBeInTheDocument()
  })

  it('writes an explicit choice through PATCH and never fabricates auto on open', async () => {
    const get = vi.spyOn(api.sourcePolicy, 'get').mockResolvedValue(policy())
    const set = vi.spyOn(api.sourcePolicy, 'set').mockResolvedValue(metric({ metric_type: 'price', preference: 'api' }))
    const user = userEvent.setup()
    render(<div className="ledger"><SourcePolicySection storeId="s1" marketplace="wildberries" /></div>)

    const price = await screen.findByRole('radiogroup', { name: 'Цены' })
    await user.click(within(price).getByRole('radio', { name: 'Только API' }))

    await waitFor(() => expect(set).toHaveBeenCalledWith('s1', 'price', 'api'))
    expect(get).toHaveBeenCalled()   // re-reads after write; no local auto-default fabricated
  })

  it('disables the API option for cost of goods and ad spend', async () => {
    vi.spyOn(api.sourcePolicy, 'get').mockResolvedValue(policy())
    render(<div className="ledger"><SourcePolicySection storeId="s1" marketplace="wildberries" /></div>)

    const cogs = await screen.findByRole('radiogroup', { name: 'Себестоимость' })
    expect(within(cogs).getByRole('radio', { name: 'Только API' })).toBeDisabled()
    expect(screen.getByText(/Маркетплейс не знает эти данные/)).toBeInTheDocument()
  })

  it('disables the API option for Yandex finance and explains why', async () => {
    vi.spyOn(api.sourcePolicy, 'get').mockResolvedValue(policy({
      revenue: { api_supported: false, limitation: 'yandex_finance_unsupported' },
    }, 'yandex'))
    render(<div className="ledger"><SourcePolicySection storeId="s1" marketplace="yandex" /></div>)

    const revenue = await screen.findByRole('radiogroup', { name: 'Выручка' })
    expect(within(revenue).getByRole('radio', { name: 'Только API' })).toBeDisabled()
    expect(screen.getByText(/Финансовая синхронизация Яндекс Маркета пока недоступна/)).toBeInTheDocument()
  })
})

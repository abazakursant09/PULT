import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StoreFinanceSummary } from '@/components/stores/StoreFinanceSummary'
import { api, type StoreFinanceSummaryOut } from '@/lib/api'

// PULT-LAUNCH-1.4.5I-QA2 — the store finance summary shows the REAL resolved total, its source, its
// completeness, and an API-vs-CSV REVENUE conflict — resolved by a real source-policy PATCH, no sum.

const sum = (o: Partial<StoreFinanceSummaryOut> = {}): StoreFinanceSummaryOut => ({
  store_id: 's1', revenue: 100, net_profit: 30, unassigned_revenue: 0,
  source: 'csv', completeness: 'complete', missing_fields: [], conflict: false,
  conflict_candidates: null, ...o,
})

beforeEach(() => { vi.restoreAllMocks() })

describe('StoreFinanceSummary', () => {
  it('renders the source of the store money total from the real endpoint', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue(sum({ source: 'csv' }))
    render(<div className="ledger"><StoreFinanceSummary storeId="s1" /></div>)
    expect(await screen.findByText(/Источник финансового расчёта магазина: CSV/)).toBeInTheDocument()
  })

  it('shows a revenue conflict with BOTH values and never a sum', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue(sum({
      source: 'csv', conflict: true, conflict_candidates: { api: 250, csv: 100 },
    }))
    render(<div className="ledger"><StoreFinanceSummary storeId="s1" /></div>)
    expect(await screen.findByText(/Есть расхождение API и CSV/)).toBeInTheDocument()
    expect(screen.getAllByText(/250/).length).toBeGreaterThan(0)   // API candidate
    expect(screen.getAllByText(/100/).length).toBeGreaterThan(0)   // CSV candidate + safe value
    expect(screen.queryByText(/350/)).toBeNull()                   // never summed
  })

  it('choosing API PATCHes revenue policy and re-reads the summary', async () => {
    const fs = vi.spyOn(api.marketplaceStores, 'financeSummary')
      .mockResolvedValueOnce(sum({ conflict: true, conflict_candidates: { api: 250, csv: 100 } }))
      .mockResolvedValueOnce(sum({ source: 'api', revenue: 250, net_profit: null,
        completeness: 'incomplete', missing_fields: ['cogs'] }))
    const set = vi.spyOn(api.sourcePolicy, 'set').mockResolvedValue({
      metric_type: 'revenue', preference: 'api', api_supported: true, api_available: true, limitation: null })
    const user = userEvent.setup()
    render(<div className="ledger"><StoreFinanceSummary storeId="s1" /></div>)

    await user.click(await screen.findByRole('button', { name: 'Использовать API' }))
    await waitFor(() => expect(set).toHaveBeenCalledWith('s1', 'revenue', 'api'))
    expect(fs).toHaveBeenCalledTimes(2)   // re-read after PATCH
  })

  it('a failed PATCH does not show a false success', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue(sum({
      conflict: true, conflict_candidates: { api: 250, csv: 100 } }))
    vi.spyOn(api.sourcePolicy, 'set').mockRejectedValue(new Error('HTTP 500'))
    const user = userEvent.setup()
    render(<div className="ledger"><StoreFinanceSummary storeId="s1" /></div>)

    await user.click(await screen.findByRole('button', { name: 'Использовать CSV' }))
    // still the CSV safe value, still a conflict banner — nothing claims it resolved to API
    expect(await screen.findByText(/Есть расхождение API и CSV/)).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/HTTP 500/)
  })

  it('null profit reads as "недостаточно данных", never 0', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue(sum({
      source: 'api', revenue: 250, net_profit: null, completeness: 'incomplete', missing_fields: ['cogs'] }))
    render(<div className="ledger"><StoreFinanceSummary storeId="s1" /></div>)
    expect(await screen.findByText(/Недостаточно данных для расчёта/)).toBeInTheDocument()
    expect(screen.getByText(/Нет данных о себестоимости\. Прибыль и маржа не рассчитаны\./)).toBeInTheDocument()
    // profit is not shown as 0
    const profitBlock = screen.getByText('Прибыль').parentElement
    expect(profitBlock?.textContent || '').not.toMatch(/(^|[^\d])0 ₽/)
  })
})

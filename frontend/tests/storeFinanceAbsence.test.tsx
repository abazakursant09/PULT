import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StoreFinanceSummary } from '@/components/stores/StoreFinanceSummary'
import { api } from '@/lib/api'

beforeEach(() => { vi.restoreAllMocks() })

describe('store finance absence contract', () => {
  it('shows explicit no data without guessing CSV or displaying a zero profit', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue({
      store_id: 'absence', revenue: null, net_profit: null, source: null,
      completeness: 'no_data', missing_fields: ['revenue'], unassigned_revenue: 0,
      conflict: false, conflict_candidates: null,
    })
    const write = vi.spyOn(api.sourcePolicy, 'set')
    render(<StoreFinanceSummary storeId="absence" />)
    expect(await screen.findByText('Источник финансового расчёта магазина: не определён.')).toBeInTheDocument()
    expect(screen.getByText('Нет данных.')).toBeInTheDocument()
    expect(screen.getByText('Недостаточно данных для расчёта')).toBeInTheDocument()
    expect(screen.getByText('Выручка').parentElement).toHaveTextContent('—')
    expect(screen.queryByText(/0 ₽|Источник финансового расчёта магазина: CSV/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(write).not.toHaveBeenCalled()
  })

  it.each(['csv', 'api'] as const)('preserves a real zero and confirmed %s source', async source => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue({
      store_id: 'zero', revenue: 0, net_profit: 0, source,
      completeness: 'complete', missing_fields: [], unassigned_revenue: 0,
      conflict: false, conflict_candidates: null,
    })
    render(<StoreFinanceSummary storeId="zero" />)
    expect(await screen.findByText(`Источник финансового расчёта магазина: ${source.toUpperCase()}.`)).toBeInTheDocument()
    expect(screen.getAllByText('0 ₽')).toHaveLength(2)
    expect(screen.queryByText('Нет данных.')).not.toBeInTheDocument()
  })
})

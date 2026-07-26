import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StorePage from '@/app/dashboard/stores/[storeId]/page'
import { api } from '@/lib/api'
import type { StoreImportsPage, StoreProductsPage } from '@/lib/api'

// One store: its products and its uploads. Both come from the 1.4.5B endpoints, and neither
// promises a number the backend does not produce.

const STORE = { id: 'st-1', label: 'Москва — FBS', marketplace: 'yandex', status: 'active' }

function products(over: Partial<StoreProductsPage> = {}): StoreProductsPage {
  return {
    store: STORE,
    items: [{
      product_id: 'p-1', sku: 'SKU-1042', name: 'Кофе зерновой',
      placement_status: 'active', placement_source: 'csv',
      first_seen_at: '2026-06-01T10:00:00', last_seen_at: '2026-07-01T10:00:00',
    }],
    page: 1, page_size: 25, total: 1, pages: 1, ...over,
  }
}

function imports(over: Partial<StoreImportsPage> = {}): StoreImportsPage {
  return {
    store: STORE,
    items: [{
      import_id: 'imp-1', filename: 'report.csv', import_type: 'products', status: 'confirmed',
      created_at: '2026-07-24T10:00:00', confirmed_at: '2026-07-24T10:01:00',
      total_rows: 10, imported_count: 9, skipped_rows: 1,
      conflicts: 0, unassigned: 0, has_unresolved_conflicts: false, source: 'csv',
    }],
    page: 1, page_size: 25, total: 1, pages: 1, ...over,
  }
}

describe('store page', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // The store page now shows a source-policy section + a finance summary (1.4.5I/QA2); give them
    // honest empty/CSV data so these product/upload tests exercise the catalog surfaces cleanly.
    vi.spyOn(api.sourcePolicy, 'get').mockResolvedValue({ store_id: 'st-1', marketplace: 'wildberries', metrics: [] })
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue({
      store_id: 'st-1', revenue: 0, net_profit: 0, unassigned_revenue: 0, source: 'csv',
      completeness: 'complete', missing_fields: [], conflict: false, conflict_candidates: null })
  })

  it('lists the products of THIS store', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports())
    const list = vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue(products())
    render(<StorePage params={{ storeId: 'st-1' }} />)

    expect(await screen.findByText('Кофе зерновой')).toBeInTheDocument()
    await waitFor(() => expect(list).toHaveBeenCalledWith('st-1', expect.objectContaining({ page: 1 })))
  })

  it('keeps a product with no metrics visible, and promises no metric column', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports())
    vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue(products({
      items: [{
        product_id: 'p-2', sku: null, name: 'Без показателей',
        placement_status: 'active', placement_source: 'csv',
        first_seen_at: '2026-06-01T10:00:00', last_seen_at: '2026-06-01T10:00:00',
      }],
    }))
    const { container } = render(<StorePage params={{ storeId: 'st-1' }} />)

    expect(await screen.findByText('Без показателей')).toBeInTheDocument()
    // The PRODUCTS table promises no per-product metric column (the store-level «Финансовый итог» is
    // a separate, real section — 1.4.5I/QA2). Assert the products column header carries no metric.
    const colhead = container.querySelector('.l-colhead')?.textContent ?? ''
    for (const promised of ['Выручка', 'Прибыль', 'Остаток', 'Рейтинг']) {
      expect(colhead).not.toContain(promised)
    }
  })

  it('says plainly when a store has no products yet', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports())
    vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue(products({ items: [], total: 0, pages: 0 }))
    render(<StorePage params={{ storeId: 'st-1' }} />)
    expect(await screen.findByText(/В этом магазине пока нет товаров/)).toBeInTheDocument()
  })

  it('pages through products without repeating a row', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports())
    const list = vi.spyOn(api.marketplaceStores, 'products')
      .mockResolvedValueOnce(products({ total: 40, pages: 2 }))
      .mockResolvedValueOnce(products({
        page: 2, total: 40, pages: 2,
        items: [{
          product_id: 'p-9', sku: 'SKU-9', name: 'Вторая страница',
          placement_status: 'active', placement_source: 'csv',
          first_seen_at: '2026-06-01T10:00:00', last_seen_at: '2026-06-01T10:00:00',
        }],
      }))
    const user = userEvent.setup()
    render(<StorePage params={{ storeId: 'st-1' }} />)
    await screen.findByText('Кофе зерновой')

    await user.click(screen.getByRole('button', { name: 'Дальше' }))
    expect(await screen.findByText('Вторая страница')).toBeInTheDocument()
    expect(screen.queryByText('Кофе зерновой')).toBeNull()
    expect(list).toHaveBeenLastCalledWith('st-1', expect.objectContaining({ page: 2 }))
  })

  it('shows the upload history of this store', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports())
    vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue(products())
    render(<StorePage params={{ storeId: 'st-1' }} />)
    expect(await screen.findByText('report.csv')).toBeInTheDocument()
    expect(screen.getByText('Товары')).toBeInTheDocument()
  })

  it('offers the conflict screen only when conflicts are unresolved', async () => {
    vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue(products())
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports())
    const { unmount } = render(<StorePage params={{ storeId: 'st-1' }} />)
    await screen.findByText('report.csv')
    expect(screen.queryByRole('link', { name: /разобрать/i })).toBeNull()
    unmount()

    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports({
      items: [{ ...imports().items[0], conflicts: 6, has_unresolved_conflicts: true }],
    }))
    render(<StorePage params={{ storeId: 'st-1' }} />)
    const link = await screen.findByRole('link', { name: /разобрать/i })
    expect(link.getAttribute('href')).toBe('/dashboard/imports/imp-1/conflicts')
  })

  it('keeps an archived store readable but takes the upload away', async () => {
    const archived = { ...STORE, status: 'archived' }
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(imports({ store: archived }))
    vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue(products({ store: archived }))
    render(<StorePage params={{ storeId: 'st-1' }} />)

    expect(await screen.findByText(/Магазин в архиве/)).toBeInTheDocument()
    expect(screen.getByText('Кофе зерновой')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Загрузить CSV' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Восстановить' })).toBeInTheDocument()
  })

  it('says the store is missing instead of showing an empty page', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockRejectedValue(new Error('HTTP 404: Магазин не найден'))
    render(<StorePage params={{ storeId: 'nope' }} />)
    expect(await screen.findByText(/Магазин не найден/)).toBeInTheDocument()
  })
})

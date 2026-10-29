import { StrictMode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StoreProductsTable } from '@/components/stores/StoreProductsTable'
import { StoreImportsTable } from '@/components/stores/StoreImportsTable'
import { api, type StoreImportsPage, type StoreProductsPage } from '@/lib/api'

type Kind = 'products' | 'imports'
type Page = StoreProductsPage | StoreImportsPage

function page(kind: Kind, storeId: string, number: number, label: string): Page {
  const store = { id: storeId, label: storeId, marketplace: 'wildberries', status: 'active' }
  const common = { store, page: number, page_size: 25, total: 75, pages: 3 }
  return kind === 'products'
    ? { ...common, items: [{ product_id: label, name: label, sku: `SKU-${label}`, placement_status: 'active', placement_source: 'csv', first_seen_at: '2026-07-01', last_seen_at: '2026-07-01' }] }
    : { ...common, items: [{ import_id: label, filename: label, import_type: 'finance', status: 'confirmed', created_at: '2026-07-01', confirmed_at: '2026-07-01', total_rows: 1, imported_count: 1, skipped_rows: 0, conflicts: 0, unassigned: 0, has_unresolved_conflicts: false, source: 'csv' }] }
}

function deferred() {
  let resolve!: (value: Page) => void
  let reject!: (error: Error) => void
  const promise = new Promise<Page>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

describe.each([
  { kind: 'products' as const, Table: StoreProductsTable, loading: 'Загружаем товары…' },
  { kind: 'imports' as const, Table: StoreImportsTable, loading: 'Загружаем историю…' },
])('$kind request ordering', ({ kind, Table, loading }) => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('hides prior-page rows and pagination until the requested page arrives', async () => {
    const pending = deferred()
    const read = vi.spyOn(api.marketplaceStores, kind)
      .mockResolvedValueOnce(page(kind, 'a', 1, 'FIRST'))
      .mockReturnValueOnce(pending.promise)
    const user = userEvent.setup()
    render(<Table storeId="a" />)
    await screen.findByText('FIRST', { exact: true })
    await user.click(screen.getByRole('button', { name: 'Дальше', exact: true }))
    expect(screen.queryByText('FIRST', { exact: true })).toBeNull()
    expect(screen.getByText(loading)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Дальше', exact: true })).toBeNull()
    expect(read).toHaveBeenCalledTimes(2)
    await act(async () => pending.resolve(page(kind, 'a', 2, 'SECOND')))
    expect(await screen.findByText('SECOND', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('Стр. 2 из 3')).toBeInTheDocument()
    expect(read).toHaveBeenLastCalledWith('a', { page: 2, page_size: 25 })
  })

  it('retries a failed page without changing the requested page', async () => {
    const pending = deferred()
    const read = vi.spyOn(api.marketplaceStores, kind)
      .mockResolvedValueOnce(page(kind, 'a', 1, 'FIRST'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockReturnValueOnce(pending.promise)
    const user = userEvent.setup()
    render(<Table storeId="a" />)
    await screen.findByText('FIRST', { exact: true })
    await user.click(screen.getByRole('button', { name: 'Дальше', exact: true }))
    await screen.findByText(/Не удалось загрузить/)
    expect(screen.queryByText('FIRST', { exact: true })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Повторить', exact: true }))
    expect(screen.getByText(loading)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить', exact: true })).toBeNull()
    await act(async () => pending.resolve(page(kind, 'a', 2, 'RECOVERED')))
    expect(await screen.findByText('RECOVERED', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('Стр. 2 из 3')).toBeInTheDocument()
    expect(read).toHaveBeenNthCalledWith(3, 'a', { page: 2, page_size: 25 })
  })

  it.each(['success', 'failure'] as const)('resets the store to page1 and ignores late old-store %s', async outcome => {
    const pending = deferred()
    const read = vi.spyOn(api.marketplaceStores, kind)
      .mockResolvedValueOnce(page(kind, 'a', 1, 'FIRST'))
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(page(kind, 'b', 1, 'CURRENT'))
    const user = userEvent.setup()
    const view = render(<Table storeId="a" />)
    await screen.findByText('FIRST', { exact: true })
    await user.click(screen.getByRole('button', { name: 'Дальше', exact: true }))
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2))
    view.rerender(<Table storeId="b" />)
    await screen.findByText('CURRENT', { exact: true })
    await act(async () => {
      if (outcome === 'success') pending.resolve(page(kind, 'a', 2, 'STALE'))
      else pending.reject(new Error('old-store failure'))
    })
    expect(screen.getByText('CURRENT', { exact: true })).toBeInTheDocument()
    expect(screen.queryByText('STALE', { exact: true })).toBeNull()
    expect(screen.queryByText(/Не удалось загрузить/)).toBeNull()
    expect(screen.getByText('Стр. 1 из 3')).toBeInTheDocument()
    expect(read).toHaveBeenLastCalledWith('b', { page: 1, page_size: 25 })
  })

  it.each(['success', 'failure'] as const)('ignores stale same-store %s after StrictMode effect cleanup', async outcome => {
    const old = deferred(), current = deferred()
    const read = vi.spyOn(api.marketplaceStores, kind)
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise)
    render(<StrictMode><Table storeId="a" /></StrictMode>)
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2))
    await act(async () => current.resolve(page(kind, 'a', 1, 'CURRENT')))
    await screen.findByText('CURRENT', { exact: true })
    await act(async () => {
      if (outcome === 'success') old.resolve(page(kind, 'a', 1, 'STALE'))
      else old.reject(new Error('old request failure'))
    })
    expect(screen.getByText('CURRENT', { exact: true })).toBeInTheDocument()
    expect(screen.queryByText('STALE', { exact: true })).toBeNull()
    expect(screen.queryByText(/Не удалось загрузить/)).toBeNull()
  })
})

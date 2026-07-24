import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ConflictsPage from '@/app/dashboard/imports/[importId]/conflicts/page'
import { api } from '@/lib/api'
import type { ImportConflictRow } from '@/lib/api'

// Three actions, because the backend implements three. There is no per-row keep/overwrite — that
// choice belongs to the whole file and was already made at import.

const ROW: ImportConflictRow = {
  row_type: 'products', row_id: 'row-1', sku: 'SKU-1042', title: 'Кофе зерновой',
  marketplace: 'wildberries', marketplace_store_id: 'st-1',
  candidates: [{ product_id: 'p-1', sku: 'SKU-1042', name: 'Кофе зерновой 1 кг' }],
}

describe('conflict resolver', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('offers exactly the three actions the backend supports', async () => {
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValue([ROW])
    render(<ConflictsPage params={{ importId: 'imp-1' }} />)

    expect(await screen.findByRole('button', { name: 'Связать с товаром' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Создать новый товар' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Оставить без товара' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Перезаписать/ })).toBeNull()
  })

  it('will not link without a product chosen', async () => {
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValue([ROW])
    const resolve = vi.spyOn(api.csvImport, 'resolveConflict')
    const user = userEvent.setup()
    render(<ConflictsPage params={{ importId: 'imp-1' }} />)

    await user.click(await screen.findByRole('button', { name: 'Связать с товаром' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Выберите товар/)
    expect(resolve).not.toHaveBeenCalled()
  })

  it('links the row to the chosen product', async () => {
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValue([ROW])
    const resolve = vi.spyOn(api.csvImport, 'resolveConflict')
      .mockResolvedValue({ row_id: 'row-1', link_status: 'linked', product_id: 'p-1' })
    const user = userEvent.setup()
    render(<ConflictsPage params={{ importId: 'imp-1' }} />)

    await user.selectOptions(await screen.findByLabelText('Товар кабинета'), 'p-1')
    await user.click(screen.getByRole('button', { name: 'Связать с товаром' }))

    await waitFor(() => expect(resolve).toHaveBeenCalledWith('imp-1',
      { row_id: 'row-1', action: 'link_existing', product_id: 'p-1' }))
  })

  it('creates a new product, and leaves a row unassigned', async () => {
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValue([ROW])
    const resolve = vi.spyOn(api.csvImport, 'resolveConflict')
      .mockResolvedValue({ row_id: 'row-1', link_status: 'linked', product_id: 'p-9' })
    const user = userEvent.setup()
    render(<ConflictsPage params={{ importId: 'imp-1' }} />)

    await user.click(await screen.findByRole('button', { name: 'Создать новый товар' }))
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('imp-1', { row_id: 'row-1', action: 'create_new' }))

    await user.click(screen.getByRole('button', { name: 'Оставить без товара' }))
    await waitFor(() => expect(resolve).toHaveBeenLastCalledWith('imp-1',
      { row_id: 'row-1', action: 'leave_unassigned' }))
  })

  it('re-reads the stored state after every decision', async () => {
    const conflicts = vi.spyOn(api.csvImport, 'conflicts')
      .mockResolvedValueOnce([ROW])
      .mockResolvedValueOnce([])
    vi.spyOn(api.csvImport, 'resolveConflict')
      .mockResolvedValue({ row_id: 'row-1', link_status: 'unassigned', product_id: null })
    const user = userEvent.setup()
    render(<ConflictsPage params={{ importId: 'imp-1' }} />)

    await user.click(await screen.findByRole('button', { name: 'Оставить без товара' }))
    expect(await screen.findByText('Все строки этой загрузки разобраны.')).toBeInTheDocument()
    expect(conflicts).toHaveBeenCalledTimes(2)
  })

  it('reports a failed decision without pretending it applied', async () => {
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValue([ROW])
    vi.spyOn(api.csvImport, 'resolveConflict').mockRejectedValue(new Error('HTTP 500'))
    const user = userEvent.setup()
    render(<ConflictsPage params={{ importId: 'imp-1' }} />)

    await user.click(await screen.findByRole('button', { name: 'Оставить без товара' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Не удалось применить решение/)
  })
})

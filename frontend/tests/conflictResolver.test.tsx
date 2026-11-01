import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
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
const route = vi.hoisted(() => ({ importId: 'imp-1' }))
vi.mock('next/navigation', () => ({ useParams: () => ({ importId: route.importId }) }))

describe('conflict resolver', () => {
  beforeEach(() => { vi.restoreAllMocks(); route.importId = 'imp-1' })

  it('loads route B while A GET is pending and ignores late A', async () => {
    let finishA!: (rows: ImportConflictRow[]) => void
    const list = vi.spyOn(api.csvImport, 'conflicts')
      .mockReturnValueOnce(new Promise(r => { finishA = r }))
      .mockResolvedValueOnce([{ ...ROW, row_id: 'B', sku: 'SKU-B' }])
    const view = render(<ConflictsPage />)
    route.importId = 'imp-B'; view.rerender(<ConflictsPage />)
    expect(list).toHaveBeenLastCalledWith('imp-B')
    await screen.findByText('SKU-B')
    await act(async () => finishA([ROW]))
    expect(screen.getByText('SKU-B')).toBeInTheDocument()
    expect(screen.queryByText(ROW.sku!)).not.toBeInTheDocument()
  })

  for (const outcome of ['success', 'rejection'] as const) {
    it(`ignores an old POST ${outcome} after route change, including its finally`, async () => {
      let finish!: (value: { row_id: string; link_status: string; product_id: null }) => void
      let reject!: (reason: Error) => void
      const list = vi.spyOn(api.csvImport, 'conflicts').mockResolvedValueOnce([ROW]).mockResolvedValueOnce([])
      vi.spyOn(api.csvImport, 'resolveConflict').mockReturnValue(new Promise((yes, no) => { finish = yes; reject = no }))
      const view = render(<ConflictsPage />)
      fireEvent.click(await screen.findByRole('button', { name: 'Создать новый товар' }))
      route.importId = 'imp-B'; view.rerender(<ConflictsPage />)
      await screen.findByText('В этой загрузке нет неразобранных строк.')
      await act(async () => {
        if (outcome === 'success') finish({ row_id: ROW.row_id, link_status: 'linked', product_id: null })
        else reject(new Error('old POST rejected'))
      })
      expect(list).toHaveBeenCalledTimes(2)
      expect(screen.getByText('В этой загрузке нет неразобранных строк.')).toBeInTheDocument()
      expect(screen.queryByText('Все строки этой загрузки разобраны.')).not.toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  }

  it('keeps the new route busy when an old POST finally settles', async () => {
    const finishes: Array<(value: { row_id: string; link_status: string; product_id: null }) => void> = []
    const post = vi.spyOn(api.csvImport, 'resolveConflict').mockImplementation(() => new Promise(r => { finishes.push(r) }))
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValue([ROW])
    const view = render(<ConflictsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Создать новый товар' }))
    route.importId = 'imp-B'; view.rerender(<ConflictsPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Создать новый товар' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Создать новый товар' }))
    await act(async () => finishes[0]({ row_id: ROW.row_id, link_status: 'linked', product_id: null }))
    expect(screen.getByRole('button', { name: 'Создать новый товар' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Создать новый товар' }))
    expect(post).toHaveBeenCalledTimes(2)
    await act(async () => finishes[1]({ row_id: ROW.row_id, link_status: 'linked', product_id: null }))
  })

  it('StrictMode cleanup starts an owned read rather than stalling behind the old guard', async () => {
    const finishes: Array<(rows: ImportConflictRow[]) => void> = []
    vi.spyOn(api.csvImport, 'conflicts').mockImplementation(() => new Promise(r => { finishes.push(r) }))
    render(<StrictMode><ConflictsPage /></StrictMode>)
    expect(finishes).toHaveLength(2)
    await act(async () => finishes[1]([ROW]))
    expect(screen.getByText(ROW.sku!)).toBeInTheDocument()
    await act(async () => finishes[0]([]))
    expect(screen.getByText(ROW.sku!)).toBeInTheDocument()
  })

  it('reconciles a lost POST response using GET only without claiming unchanged', async () => {
    let reject!: (reason: Error) => void
    let read!: (rows: ImportConflictRow[]) => void
    const list = vi.spyOn(api.csvImport, 'conflicts').mockResolvedValueOnce([ROW])
      .mockReturnValueOnce(new Promise(r => { read = r }))
    const post = vi.spyOn(api.csvImport, 'resolveConflict').mockReturnValue(new Promise((_, r) => { reject = r }))
    render(<ConflictsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Создать новый товар' }))
    await act(async () => reject(new Error('response lost after commit')))
    expect(screen.queryByText(/Строка осталась без изменений/)).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Результат решения не подтверждён')
    expect(list).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Загружаем строки…')).toBeInTheDocument()
    expect(screen.queryByText('Все строки этой загрузки разобраны.')).not.toBeInTheDocument()
    await act(async () => read([]))
    expect(post).toHaveBeenCalledTimes(1)
    expect(screen.getByText('В этой загрузке нет неразобранных строк.')).toBeInTheDocument()
  })

  it('keeps uncertain outcome visible after failed recovery and retries GET only', async () => {
    let finishRetry!: (rows: ImportConflictRow[]) => void
    const list = vi.spyOn(api.csvImport, 'conflicts').mockResolvedValueOnce([ROW])
      .mockRejectedValueOnce(new Error('GET failed')).mockReturnValueOnce(new Promise(r => { finishRetry = r }))
    const post = vi.spyOn(api.csvImport, 'resolveConflict').mockRejectedValue(new Error('POST uncertain'))
    render(<ConflictsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Создать новый товар' }))
    await screen.findByText('Не удалось загрузить конфликты этой загрузки.')
    expect(screen.getByRole('alert')).toHaveTextContent('Результат решения не подтверждён')
    expect(screen.queryByText(/Строка осталась без изменений|Все строки этой загрузки разобраны/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить', exact: true }))
    expect(screen.getByText('Загружаем строки…')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Результат решения не подтверждён')
    expect(screen.queryByRole('button', { name: 'Создать новый товар' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить', exact: true })).not.toBeInTheDocument()
    await act(async () => finishRetry([ROW]))
    await screen.findByRole('button', { name: 'Создать новый товар' })
    expect(list).toHaveBeenCalledTimes(3)
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('serializes A/B decisions and blocks duplicates until the confirming GET finishes', async () => {
    const other = { ...ROW, row_id: 'row-2', sku: 'SKU-B' }
    let finishPost!: (value: { row_id: string; link_status: string; product_id: null }) => void
    let finishRead!: (rows: ImportConflictRow[]) => void
    vi.spyOn(api.csvImport, 'conflicts').mockResolvedValueOnce([ROW, other])
      .mockReturnValueOnce(new Promise(r => { finishRead = r }))
    const post = vi.spyOn(api.csvImport, 'resolveConflict').mockReturnValue(new Promise(r => { finishPost = r }))
    render(<ConflictsPage />)
    const buttons = await screen.findAllByRole('button', { name: 'Создать новый товар' })
    act(() => { fireEvent.click(buttons[0]); fireEvent.click(buttons[1]); fireEvent.click(buttons[0]) })
    expect(post).toHaveBeenCalledTimes(1)
    expect(buttons.every(b => (b as HTMLButtonElement).disabled)).toBe(true)
    await act(async () => finishPost({ row_id: ROW.row_id, link_status: 'linked', product_id: null }))
    expect(screen.queryByRole('button', { name: 'Создать новый товар' })).not.toBeInTheDocument()
    await act(async () => finishRead([other]))
    expect(screen.getByRole('button', { name: 'Создать новый товар' })).toBeEnabled()
    expect(post).toHaveBeenCalledTimes(1)
  })

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
    expect(await screen.findByRole('alert')).toHaveTextContent(/Результат решения не подтверждён/)
  })
})

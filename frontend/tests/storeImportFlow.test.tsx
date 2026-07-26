import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StoreImportPage from '@/app/dashboard/stores/[storeId]/import/page'
import { api } from '@/lib/api'
import type { ImportPreviewResponse } from '@/lib/api'

// The CSV flow, store-scoped.
//
// The rule these tests hold: the FILE decides the report type, not the seller. The choice before
// the upload is a hint for the human; everything after it — the overwrite wording, the confirm —
// follows `preview.import_type`.

const STORE = { id: 'st-1', label: 'Москва — FBS', marketplace: 'wildberries', status: 'active' }
const importsPage = { store: STORE, items: [], page: 1, page_size: 1, total: 0, pages: 0 } as never

function preview(over: Partial<ImportPreviewResponse> = {}): ImportPreviewResponse {
  return {
    import_id: 'imp-1', marketplace: 'wildberries', import_type: 'finance',
    total_rows: 100, valid_rows: 100, skipped_rows: 0,
    new_products: 0, updates: 100, conflicts: 0, unassigned: 0, rows_to_replace: 0,
    headers: [], mapped_columns: {}, unmapped_required: [], preview_rows: [],
    warnings: [], errors: [], file_hash: 'h', duplicate_import_id: null, duplicate_date: null,
    ...over,
  }
}

async function reachPreview(user: ReturnType<typeof userEvent.setup>, container: HTMLElement) {
  await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
  await user.upload(
    container.querySelector('input[type="file"]') as HTMLInputElement,
    new File(['a,b\n1,2'], 'report.csv', { type: 'text/csv' }),
  )
  await user.click(screen.getByRole('button', { name: /Проверить файл/i }))
}

describe('store-scoped CSV flow', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(importsPage)
    // a confirmed finance import already exists, so all four types are open
    vi.spyOn(api.csvImport, 'history').mockResolvedValue([
      { id: 'i0', filename: 'f.csv', marketplace: 'wb', import_type: 'finance', status: 'confirmed',
        total_rows: 1, imported_count: 1, created_at: '2026-07-01', confirmed_at: '2026-07-01' },
    ])
  })

  it('offers every report type the backend can parse', async () => {
    render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    for (const label of ['Товары', 'Финансы', 'Возвраты', 'Данные карточек товаров']) {
      expect(await screen.findByRole('radio', { name: new RegExp(label) })).toBeInTheDocument()
    }
  })

  it('offers a template only for a pair that really has one', async () => {
    const user = userEvent.setup()
    render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    // Wildberries + Товары has a template…
    await user.click(await screen.findByRole('radio', { name: /Товары/ }))
    expect(screen.getByRole('link', { name: 'Скачать шаблон' })).toBeInTheDocument()
    // …Wildberries + Возвраты does not, and the page says so instead of linking to a 404.
    await user.click(screen.getByRole('radio', { name: /Возвраты/ }))
    expect(screen.queryByRole('link', { name: 'Скачать шаблон' })).toBeNull()
    expect(screen.getByText(/Шаблон для этого отчёта пока не готов/)).toBeInTheDocument()
  })

  it('always sends the store with the file', async () => {
    const upload = vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview())
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await reachPreview(user, container)
    await waitFor(() => expect(upload).toHaveBeenCalled())
    expect(upload.mock.calls[0][1]).toBe('st-1')
  })

  it('warns when the file turned out to be a different report than the one chosen', async () => {
    // seller picked Товары; the parser read a finance export
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview({ import_type: 'finance' }))
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await user.click(await screen.findByRole('radio', { name: /Товары/ }))
    await reachPreview(user, container)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Файл распознан как «Финансы»/)
    expect(alert).toHaveTextContent(/вы выбрали «Товары»/)
    expect(alert).toHaveTextContent(/по содержимому файла/)
  })

  it('says nothing when the file matches the choice', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview({ import_type: 'finance' }))
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await user.click(await screen.findByRole('radio', { name: /Финансы/ }))
    await reachPreview(user, container)

    expect(await screen.findByText('Проверка файла')).toBeInTheDocument()
    expect(screen.queryByText(/Файл распознан как/)).toBeNull()
  })

  it('explains overwrite by the DETECTED type, not by the chosen one', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview({ import_type: 'card_content', rows_to_replace: 12 }))
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await user.click(await screen.findByRole('radio', { name: /Товары/ }))
    await reachPreview(user, container)

    // card_content is replaced cabinet-wide by SKU — that is what the seller must be told,
    // even though they had picked "Товары" beforehand.
    expect(await screen.findByText(/во всём кабинете/)).toBeInTheDocument()
    expect(screen.getByText('Будет удалено строк: 12')).toBeInTheDocument()
  })

  it('never promises to replace "the same file"', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview({ import_type: 'finance' }))
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await reachPreview(user, container)

    const text = container.textContent ?? ''
    expect(await screen.findByText(/за дни, которые есть в файле/)).toBeInTheDocument()
    expect(text).not.toMatch(/прошлую загрузку этого же файла/)
    expect(text).toContain('Удалять нечего')
  })

  it('sends the chosen mode to confirm', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview({ rows_to_replace: 4 }))
    const confirm = vi.spyOn(api.csvImport, 'confirm')
      .mockResolvedValue({ import_id: 'imp-1', imported_count: 100, skipped_count: 0 })
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await reachPreview(user, container)

    await user.click(await screen.findByRole('radio', { name: /Заменить данные/ }))
    await user.click(screen.getByRole('button', { name: /Импортировать 100 строк/i }))
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('imp-1', 'overwrite'))
  })

  it('reports the numbers the server returned, and offers the conflicts it found', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview({ conflicts: 6 }))
    vi.spyOn(api.csvImport, 'confirm').mockResolvedValue({
      import_id: 'imp-1', imported_count: 94, skipped_count: 0, conflicts: 6,
    })
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await reachPreview(user, container)
    await user.click(await screen.findByRole('button', { name: /Импортировать 100 строк/i }))

    expect(await screen.findByText('Импорт завершён')).toBeInTheDocument()
    expect(screen.getByText('94')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Разобрать 6 строк/ })
    expect(link.getAttribute('href')).toBe('/dashboard/imports/imp-1/conflicts')
  })

  it('refuses to start on an archived store', async () => {
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(
      { ...importsPage, store: { ...STORE, status: 'archived' } } as never)
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    expect(await screen.findByText(/Магазин в архиве/)).toBeInTheDocument()
    expect(container.querySelector('input[type="file"]')).toBeNull()
  })
})

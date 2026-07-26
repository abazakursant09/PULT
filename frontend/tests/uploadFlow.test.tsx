import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StoreImportPage from '@/app/dashboard/stores/[storeId]/import/page'
import { api } from '@/lib/api'
import type { ImportConfirmResponse, ImportPreviewResponse } from '@/lib/api'

// The upload is the ONLY door into the Advisory MVP: nothing syncs on its own, so if this flow
// breaks, PULT has no data and therefore nothing to diagnose. The whole product hangs off these
// three steps — upload → preview → confirm.
//
// Since 1.4.5C the flow is store-scoped: the route names the store, the upload sends it, and the
// backend reads the marketplace from it. The guarantees under test are unchanged.

const STORE = { id: 'st-1', label: 'Москва — FBS', marketplace: 'yandex', status: 'active' }
const importsPage = { store: STORE, items: [], page: 1, page_size: 1, total: 0, pages: 0 } as never

// No `as` cast: the response type must be satisfied in full. An incomplete fixture would
// otherwise sail past the compiler and blow up at render — which is exactly what a real
// backend contract change would do to a seller mid-upload.
const preview: ImportPreviewResponse = {
  import_id: 'imp-1',
  marketplace: 'yandex',
  import_type: 'finance',
  total_rows: 120,
  valid_rows: 118,
  skipped_rows: 2,
  new_products: 0,
  updates: 118,
  conflicts: 0,
  unassigned: 0,
  rows_to_replace: 0,
  headers: ['sku', 'revenue'],
  mapped_columns: { sku: 'sku', revenue: 'revenue' },
  unmapped_required: [],
  preview_rows: [{ sku: 'ART-1001', revenue: 1000 }],
  warnings: [],
  errors: [],
  file_hash: 'deadbeef',
  duplicate_import_id: null,
  duplicate_date: null,
}

const confirmed: ImportConfirmResponse = {
  import_id: 'imp-1',
  imported_count: 118,
  skipped_count: 2,
}

function pickFile(input: HTMLElement) {
  const file = new File(['sku,revenue\nART-1001,1000\n'], 'report.csv', { type: 'text/csv' })
  return userEvent.upload(input as HTMLInputElement, file)
}

describe('CSV upload flow (the only way data enters PULT)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.setItem('token', 'test-token')
    // The page asks for the store (header) and for import history (finance-first gate) on mount.
    // Both are stubbed so no test can reach the network.
    vi.spyOn(api.csvImport, 'history').mockResolvedValue([])
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(importsPage)
  })

  it('carries a report from file to imported rows, and always names the store', async () => {
    const upload = vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview)
    const confirm = vi.spyOn(api.csvImport, 'confirm').mockResolvedValue(confirmed)
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)

    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
    await pickFile(container.querySelector('input[type="file"]') as HTMLInputElement)
    await user.click(screen.getByRole('button', { name: /Проверить файл/i }))

    // The store id travels with the file — without it the backend refuses the upload (1.4.2).
    await waitFor(() => expect(upload).toHaveBeenCalled())
    expect(upload.mock.calls[0][1]).toBe('st-1')

    expect(await screen.findByText(/Проверка файла/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Импортировать 120 строк/i }))

    await waitFor(() => expect(confirm).toHaveBeenCalledWith('imp-1', 'new'))
    expect(await screen.findByText('Импорт завершён')).toBeInTheDocument()
    expect(screen.getByText(/рекомендации появятся на главной автоматически/)).toBeInTheDocument()
  })

  it('reports a failed upload instead of pretending the data arrived', async () => {
    vi.spyOn(api.csvImport, 'upload').mockRejectedValue(new Error('HTTP 500'))
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)

    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
    await pickFile(container.querySelector('input[type="file"]') as HTMLInputElement)
    await user.click(screen.getByRole('button', { name: /Проверить файл/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Не удалось загрузить файл/)
    expect(screen.queryByText('Импорт завершён')).toBeNull()
  })

  it('reports a failed import at the confirm step', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview)
    vi.spyOn(api.csvImport, 'confirm').mockRejectedValue(new Error('HTTP 500'))
    const user = userEvent.setup()
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)

    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
    await pickFile(container.querySelector('input[type="file"]') as HTMLInputElement)
    await user.click(screen.getByRole('button', { name: /Проверить файл/i }))
    await user.click(await screen.findByRole('button', { name: /Импортировать 120 строк/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Импорт не выполнен/)
    expect(screen.queryByText('Импорт завершён')).toBeNull()
  })
})

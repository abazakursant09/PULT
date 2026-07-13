import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ImportPage from '@/app/dashboard/import/page'
import { api } from '@/lib/api'
import type { ImportConfirmResponse, ImportPreviewResponse } from '@/lib/api'
import { routerPush } from './setup'

// The upload is the ONLY door into the Advisory MVP: no marketplace connection UI exists and
// nothing syncs on its own, so if this flow breaks, PULT has no data and therefore nothing to
// diagnose. The whole product hangs off these three steps — upload → preview → confirm.
//
// Analytics fire on every step; they are stubbed so the flow, not the telemetry, is measured.
vi.mock('@/lib/analytics', () => ({
  trackEvent: vi.fn(),
  stampFunnel: vi.fn(),
  firstTimeOnly: () => false,
  elapsedSince: () => 0,
  FUNNEL_TS: { signup: 'signup', firstImport: 'firstImport' },
}))

// No `as` cast: the response type must be satisfied in full. An incomplete fixture would
// otherwise sail past the compiler and blow up at render — which is exactly what a real
// backend contract change would do to a seller mid-upload.
const preview: ImportPreviewResponse = {
  import_id: 'imp-1',
  marketplace: 'wb',
  import_type: 'finance',
  total_rows: 120,
  valid_rows: 118,
  skipped_rows: 2,
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
  })

  it('carries a report from file to imported rows, then on to the diagnosis', async () => {
    const upload = vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview)
    const confirm = vi.spyOn(api.csvImport, 'confirm').mockResolvedValue(confirmed)

    const { container } = render(<ImportPage />)

    // 1. choose a file
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await pickFile(input)

    // 2. upload → the backend parses and returns a preview
    await userEvent.click(screen.getByRole('button', { name: /Загрузить и проверить/i }))
    await waitFor(() => expect(upload).toHaveBeenCalled())
    expect(await screen.findByText('КОРРЕКТНЫХ')).toBeInTheDocument()
    expect(screen.getAllByText(/118/).length).toBeGreaterThan(0)   // valid rows, from the preview

    // 3. confirm → rows are actually imported
    const confirmBtn = screen.getAllByRole('button')
      .find((b) => /импорт|подтверд/i.test(b.textContent ?? ''))!
    await userEvent.click(confirmBtn)

    await waitFor(() => expect(confirm).toHaveBeenCalledWith('imp-1', 'new'))
    expect(await screen.findByText('Импорт завершён')).toBeInTheDocument()

    // 4. the seller is handed straight to the diagnosis — the point of the whole upload
    await userEvent.click(screen.getByRole('button', { name: /Перейти в Пульт/i }))
    expect(routerPush).toHaveBeenCalledWith('/dashboard')
  })

  it('reports a failed upload instead of pretending the data arrived', async () => {
    vi.spyOn(api.csvImport, 'upload').mockRejectedValue(new Error('Неверный формат файла'))

    const { container } = render(<ImportPage />)
    await pickFile(container.querySelector('input[type="file"]') as HTMLInputElement)
    await userEvent.click(screen.getByRole('button', { name: /Загрузить и проверить/i }))

    expect(await screen.findByText(/Неверный формат файла/)).toBeInTheDocument()
    // the seller is NOT told the import succeeded
    expect(screen.queryByText('Импорт завершён')).not.toBeInTheDocument()
  })

  it('reports a failed import at the confirm step', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(preview)
    vi.spyOn(api.csvImport, 'confirm').mockRejectedValue(new Error('Ошибка при импорте'))

    const { container } = render(<ImportPage />)
    await pickFile(container.querySelector('input[type="file"]') as HTMLInputElement)
    await userEvent.click(screen.getByRole('button', { name: /Загрузить и проверить/i }))
    await screen.findByText("КОРРЕКТНЫХ")

    const confirmBtn = screen.getAllByRole('button')
      .find((b) => /импорт|подтверд/i.test(b.textContent ?? ''))!
    await userEvent.click(confirmBtn)

    await waitFor(() =>
      expect(screen.queryByText('Импорт завершён')).not.toBeInTheDocument())
  })
})

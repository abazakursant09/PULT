import { act, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ImportedDataExport from '@/components/ImportedDataExport'

const fetchMock = vi.fn()
let click: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:test-export'), revokeObjectURL: vi.fn() })
  click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function start() {
  render(<ImportedDataExport />)
  fireEvent.click(screen.getByRole('button', { name: 'Скачать импортированные данные' }))
}

it('downloads only on successful JSON response with cookie authentication', async () => {
  vi.useFakeTimers()
  fetchMock.mockResolvedValue({ ok: true, headers: new Headers({ 'content-type': 'application/json' }), blob: async () => new Blob(['{}']) })
  start()
  await act(async () => {})
  expect(screen.getByRole('status')).toBeInTheDocument()
  expect(click).toHaveBeenCalledTimes(1)
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/data/export/imports'), expect.objectContaining({ credentials: 'include', cache: 'no-store' }))
  expect(screen.getByText(/Это не полный архив/)).toBeInTheDocument()
  act(() => { vi.advanceTimersByTime(30_000) })
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-export')
})

it.each([401, 403, 413, 429, 500])('reports HTTP %s without saving an error file', async status => {
  fetchMock.mockResolvedValue({ ok: false, status })
  start()
  await screen.findByRole('alert')
  expect(click).not.toHaveBeenCalled()
  expect(screen.getByRole('button')).not.toBeDisabled()
})

it('prevents repeat requests while pending and permits retry after a network error', async () => {
  let fail!: (reason: Error) => void
  fetchMock.mockReturnValue(new Promise((_, reject) => { fail = reject }))
  start()
  expect(screen.getByRole('button')).toBeDisabled()
  fireEvent.click(screen.getByRole('button'))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  fail(new TypeError('synthetic offline'))
  await screen.findByRole('alert')
  await waitFor(() => expect(screen.getByRole('button')).not.toBeDisabled())
})

it('rejects a successful HTML login page instead of downloading it', async () => {
  fetchMock.mockResolvedValue({ ok: true, headers: new Headers({ 'content-type': 'text/html' }) })
  start()
  await screen.findByRole('alert')
  expect(click).not.toHaveBeenCalled()
})

it('downloads saved analysis from its own endpoint and identifies its scope', async () => {
  vi.useFakeTimers()
  fetchMock.mockResolvedValue({ ok: true, headers: new Headers({ 'content-type': 'application/json' }), blob: async () => new Blob(['{}']) })
  render(<ImportedDataExport analysis />)
  fireEvent.click(screen.getByRole('button', { name: 'Скачать сохранённую аналитику' }))
  await act(async () => {})
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/data/export/analysis'), expect.objectContaining({ credentials: 'include' }))
  expect(screen.getByText(/не снимок экранной ленты/)).toBeInTheDocument()
  expect(click).toHaveBeenCalledTimes(1)
  act(() => { vi.advanceTimersByTime(30_000) })
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-export')
})

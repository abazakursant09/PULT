import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MarkingPage from '@/app/dashboard/marking/page'

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const empty = /Товаров не найдено/
const scanError = /Не удалось загрузить товары/
afterEach(() => vi.restoreAllMocks())

describe('marking transport and visible outcomes', () => {
  it('uses configured origin and cookies, and renders a genuinely empty result', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response([]))
    render(<MarkingPage />)
    expect(await screen.findByText(empty)).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(`${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/marking/scan`, expect.objectContaining({ credentials: 'include' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it.each([404, 500, 'network'] as const)('shows scan failure for %s instead of empty success', async failure => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    if (failure === 'network') fetch.mockRejectedValue(new Error('offline'))
    else fetch.mockImplementation(async () => response({ detail: 'unavailable' }, failure))
    render(<MarkingPage />)
    expect(await screen.findByText(scanError, {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByText(empty)).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(failure === 404 ? 1 : 3)
  })

  it('recovers from a failed scan on retry', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response({}, 404)).mockResolvedValueOnce(response([]))
    render(<MarkingPage />)
    await screen.findByText(scanError)
    fireEvent.click(screen.getByRole('button', { name: /Проверить все товары/ }))
    expect(await screen.findByText(empty)).toBeInTheDocument()
    expect(screen.queryByText(scanError)).not.toBeInTheDocument()
  })

  it.each([401, 403])('clears local session and shows an error for HTTP %s', async status => {
    localStorage.setItem('user', JSON.stringify({ id: 'synthetic' }))
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response({}, status))
    render(<MarkingPage />)
    await screen.findByText(scanError)
    expect(localStorage.getItem('user')).toBeNull()
    expect(screen.queryByText(empty)).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    // JSDOM cannot follow window.location navigation; cookie/redirect semantics are unchanged.
  })

  it('encodes categories, clears stale results, and never displays failed checks as success', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async url =>
      String(url).endsWith('/scan') ? response([]) : response({ category: 'A & Б', requires_marking: false }))
    render(<MarkingPage />)
    await screen.findByText(empty)
    const input = screen.getByPlaceholderText(/Введите категорию/)
    fireEvent.change(input, { target: { value: 'A & Б' } })
    fireEvent.click(screen.getByRole('button', { name: 'Проверить' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`category=${encodeURIComponent('A & Б')}`), expect.anything()))
    await waitFor(() => expect(input).not.toBeDisabled())
    expect(screen.getByText(/не требует обязательной маркировки/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fetch.mockImplementation(async () => response({}, 500))
    fireEvent.click(screen.getByRole('button', { name: 'Проверить' }))
    expect(input).toBeDisabled()
    expect(await screen.findByText(/Не удалось проверить категорию/, {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByText(/не требует обязательной маркировки/)).not.toBeInTheDocument()
  })
})

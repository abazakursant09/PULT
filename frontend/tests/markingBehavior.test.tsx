import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MarkingPage from '@/app/dashboard/marking/page'

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const empty = /Товаров не найдено/
const scanError = /Не удалось загрузить товары/
const noMatch = 'Совпадений не найдено'
const match = 'Совпадение со справочником'
afterEach(() => vi.restoreAllMocks())

describe('marking transport and visible outcomes', () => {
  it('describes an embedded keyword directory without legal, freshness or automatic-update promises', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response([]))
    render(<MarkingPage />)
    await screen.findByText(empty)
    expect(screen.getByText(/встроенный справочник ключевых слов/)).toBeInTheDocument()
    expect(screen.getByText(/не является официальным подтверждением/)).toBeInTheDocument()
    expect(screen.getByText(/Самостоятельно проверяйте действующие требования/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Примеры категорий во встроенном справочнике' })).toBeInTheDocument()
    expect(screen.getByText(/Данные получены локально/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/раз в неделю|штраф|300 000|наносить QR|Перечень актуален|последняя проверка|Требуется маркировка|не требует обязательной маркировки/i)
    expect(screen.getByText(/Время получения данных не подтверждает актуальность требований/)).toBeInTheDocument()
  })

  it('labels both populated scan groups as directory matches, retaining products and indicative regulation', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response([
      { product_id: 'match', product_name: 'Synthetic matched product', category: 'Обувь', requires_marking: true, regulation: 'SYNTHETIC_REGULATION' },
      { product_id: 'unknown', product_name: 'Synthetic unmatched product', category: null, requires_marking: false, regulation: null },
    ]))
    render(<MarkingPage />)
    await screen.findByText('Synthetic matched product')
    expect(screen.getByText('Synthetic unmatched product')).toBeInTheDocument()
    expect(screen.getByText(match, { exact: true })).toBeInTheDocument()
    expect(screen.getByText(noMatch, { exact: true })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: `${match} (1)` })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: `${noMatch} (1)` })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Ориентир из справочника' })).toBeInTheDocument()
    expect(screen.getByText('SYNTHETIC_REGULATION')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/Не требуют маркировки|Маркировка не требуется|Требуют маркировки/)
  })

  it('labels unknown input as no directory match, not absence of legal obligations', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async url => String(url).endsWith('/scan')
      ? response([]) : response({ category: 'Unknown synthetic category', requires_marking: false }))
    render(<MarkingPage />)
    await screen.findByText(empty)
    fireEvent.change(screen.getByPlaceholderText(/Введите категорию/), { target: { value: 'Unknown synthetic category' } })
    fireEvent.click(screen.getByRole('button', { name: 'Проверить' }))
    expect(await screen.findByText(`${noMatch}: «Unknown synthetic category»`)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/не требует|не требуется|нет обязательств/)
  })

  it('labels a positive check as a directory match and keeps the warning explicitly indicative', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async url => String(url).endsWith('/scan')
      ? response([]) : response({ category: 'Обувь', requires_marking: true, warning: 'SYNTHETIC_WARNING' }))
    render(<MarkingPage />)
    await screen.findByText(empty)
    fireEvent.change(screen.getByPlaceholderText(/Введите категорию/), { target: { value: 'Обувь' } })
    fireEvent.click(screen.getByRole('button', { name: 'Проверить' }))
    expect(await screen.findByText(match, { exact: true })).toBeInTheDocument()
    expect(screen.getByText('Ориентир из справочника: SYNTHETIC_WARNING')).toBeInTheDocument()
    expect(screen.queryByText(/Требуется маркировка/)).not.toBeInTheDocument()
  })
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

  it.each([401, 403])('shows HTTP %s as an error and expires the profile only for 401', async status => {
    localStorage.setItem('user', JSON.stringify({ id: 'synthetic' }))
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response({}, status))
    render(<MarkingPage />)
    await screen.findByText(scanError)
    expect(localStorage.getItem('user')).toBe(status === 401 ? null : JSON.stringify({ id: 'synthetic' }))
    expect(screen.queryByText(empty)).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    // A permission denial is not session expiry. The HttpOnly cookie remains server-owned.
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
    expect(screen.getByText(`${noMatch}: «A & Б»`)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fetch.mockImplementation(async () => response({}, 500))
    fireEvent.click(screen.getByRole('button', { name: 'Проверить' }))
    expect(input).toBeDisabled()
    expect(await screen.findByText(/Не удалось проверить категорию/, {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByText(`${noMatch}: «A & Б»`)).not.toBeInTheDocument()
  })
})

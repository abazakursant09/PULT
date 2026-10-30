import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BusinessToday from '@/components/dashboard/BusinessToday'
import { api, type TodaySummary } from '@/lib/api'
import { todayNoData, todayWithData } from './fixtures'

// "Состояние бизнеса сегодня" is the first thing a seller sees. Every number it shows is
// rendered verbatim from GET /api/today/summary — so these tests assert that the numbers on
// screen are the numbers the backend sent, and that nothing is invented when data is absent.

describe('BusinessToday', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('renders the figures the API returned', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayWithData)

    render(<BusinessToday />)

    expect(await screen.findByText('Состояние бизнеса сегодня')).toBeInTheDocument()
    // ru-RU grouping uses a non-breaking space, so match on the digits rather than the glyph
    expect(await screen.findByText(/128\D?400/)).toBeInTheDocument()   // revenue; wait for data, not the static caption
    expect(screen.getByText(/3\D?200/)).toBeInTheDocument()     // profit (negative)
    expect(screen.getByText('-2.5%')).toBeInTheDocument()       // margin
    expect(screen.getByText('-12%')).toBeInTheDocument()        // delta vs yesterday
    expect(screen.getByText('3')).toBeInTheDocument()           // loss-making products
  })

  it('keeps the caption while loading and renders only the returned figures after a delayed response', async () => {
    let resolve!: (value: TodaySummary) => void
    const response = new Promise<TodaySummary>(done => { resolve = done })
    vi.spyOn(api.today, 'getSummary').mockReturnValue(response)
    render(<BusinessToday />)

    expect(await screen.findByText('Состояние бизнеса сегодня')).toBeInTheDocument()
    expect(screen.getByLabelText('Загрузка')).toBeInTheDocument()
    expect(screen.queryByText(/128\D?400/)).not.toBeInTheDocument()
    await act(async () => { resolve(todayWithData); await response })
    expect(await screen.findByText(/128\D?400/)).toBeInTheDocument()
    expect(screen.getByText(/3\D?200/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Загрузка')).not.toBeInTheDocument()
  })

  it('says so plainly when there is not enough data, and invents no numbers', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)

    render(<BusinessToday />)

    expect(await screen.findByText('Недостаточно финансовых данных')).toBeInTheDocument()
    expect(screen.queryByText('Выручка')).not.toBeInTheDocument()
  })

  it('marks demo data as demo', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue({ ...todayWithData, is_demo: true })

    render(<BusinessToday />)

    expect(await screen.findByText('ДЕМО')).toBeInTheDocument()
  })

  it('shows the four operational stages without invented charts or forecasts', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayWithData)
    const { container } = render(<BusinessToday />)
    await screen.findByText('Продажи')
    expect(screen.getAllByRole('heading', { level: 3 }).map(n => n.textContent))
      .toEqual(['Спрос', 'Продажи', 'Маржа', 'Остатки'])
    expect(screen.getByText('29.10.2026')).toBeInTheDocument()
    expect(screen.queryByText('ДЕМО')).not.toBeInTheDocument()
    expect(screen.queryByText(/55.940|7 дней|Оценка эффекта/)).not.toBeInTheDocument()
    expect(container.querySelectorAll('article')).toHaveLength(4)
  })

  it('labels the operations feed count as signals, not unique low-stock products', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue({ ...todayWithData, low_stock_count: 17 })
    const { container } = render(<BusinessToday />)
    expect(await screen.findAllByText('Операционные сигналы')).toHaveLength(2)
    const stockCard = screen.getByRole('heading', { name: 'Остатки' }).closest('article')!
    expect(stockCard.querySelector('strong')).toHaveTextContent('17')
    const signalLabel = screen.getByText('Операционные сигналы', { selector: 'dt' })
    expect(signalLabel.nextElementSibling).toHaveTextContent('17')
    expect(container).not.toHaveTextContent(/Товар(?:ов|ы) с низким остатком/)
  })

  it('does not label missing margin as zero', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue({ ...todayWithData, margin_pct: null })
    render(<BusinessToday />)
    expect(await screen.findByText('—')).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('keeps unknown revenue and profit absent instead of showing zero rubles', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue({
      ...todayWithData, revenue_today: null, profit_today: null, margin_pct: null,
    })
    render(<BusinessToday />)
    expect(await screen.findAllByText('—')).toHaveLength(3)
    expect(screen.queryByText(/NaN|0 ₽/)).not.toBeInTheDocument()
    const profit = screen.getByText('Прибыль за период').nextElementSibling!
    expect(profit).toHaveTextContent('—')
    expect(profit).toHaveStyle({ color: 'var(--text-3)' })
  })

  it('preserves a real zero profit and revenue', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue({
      ...todayWithData, revenue_today: 0, profit_today: 0, margin_pct: null,
    })
    render(<BusinessToday />)
    expect(await screen.findAllByText('0 ₽')).toHaveLength(2)
  })

  it('surfaces a load failure instead of showing zeroes as if they were real', async () => {
    vi.spyOn(api.today, 'getSummary').mockRejectedValue(new Error('boom'))

    render(<BusinessToday />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Не удалось загрузить: boom')
    expect(alert).toHaveTextContent('Попробуйте обновить страницу')
    expect(screen.queryByText('Выручка')).not.toBeInTheDocument()
  })
})

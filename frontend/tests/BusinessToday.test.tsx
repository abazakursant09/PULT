import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import BusinessToday from '@/components/dashboard/BusinessToday'
import { api } from '@/lib/api'
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
    expect(screen.getByText(/128\D?400/)).toBeInTheDocument()   // revenue
    expect(screen.getByText(/3\D?200/)).toBeInTheDocument()     // profit (negative)
    expect(screen.getByText('-2.5%')).toBeInTheDocument()       // margin
    expect(screen.getByText('-12%')).toBeInTheDocument()        // delta vs yesterday
    expect(screen.getByText('3')).toBeInTheDocument()           // loss-making products
  })

  it('says so plainly when there is not enough data, and invents no numbers', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)

    render(<BusinessToday />)

    expect(await screen.findByText('Недостаточно данных за сегодня')).toBeInTheDocument()
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
    expect(screen.getByText('Сегодня · выручка к вчера')).toBeInTheDocument()
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

  it('surfaces a load failure instead of showing zeroes as if they were real', async () => {
    vi.spyOn(api.today, 'getSummary').mockRejectedValue(new Error('boom'))

    render(<BusinessToday />)

    await waitFor(() =>
      expect(screen.getByText(/Не удалось загрузить/)).toBeInTheDocument())
    expect(screen.queryByText('Выручка')).not.toBeInTheDocument()
  })
})

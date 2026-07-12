import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Dashboard from '@/app/dashboard/page'
import { api } from '@/lib/api'
import { diagnosisCard, secondCard, todayNoData, todayWithData } from './fixtures'
import { routerPush } from './setup'

// The dashboard is the destination of the whole MVP path: it decides whether the seller sees
// their diagnosis or the first-run screen, and it is the only place that tells a seller with
// no data what to do next. Getting that instruction wrong is not a cosmetic bug — it sends a
// paying user down a road that does not exist.

describe('Dashboard', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.setItem('token', 'test-token')
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [diagnosisCard] })
  })

  it('sends an unauthenticated visitor to login', async () => {
    localStorage.removeItem('token')
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayWithData)

    render(<Dashboard />)

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith('/login'))
  })

  it('shows the diagnosis once the seller has data', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayWithData)
    // The dashboard renders the feed with skipTopAction, so the top card is deliberately
    // withheld from the feed — TodayFocus owns it, and showing it twice would be noise.
    // Two cards therefore prove BOTH halves: the top one is dropped, the second survives.
    vi.spyOn(api.presentation, 'getCards')
      .mockResolvedValue({ cards: [diagnosisCard, secondCard] })

    render(<Dashboard />)

    expect(await screen.findByText('Состояние бизнеса сегодня')).toBeInTheDocument()
    expect(await screen.findByText(/Остаток кончится/)).toBeInTheDocument()   // 2nd card shown
  })

  it('shows the first-run screen when the seller has no data yet', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)

    render(<Dashboard />)

    expect(await screen.findByText(/Нет данных для анализа/)).toBeInTheDocument()
    // no diagnosis is invented out of nothing
    expect(screen.queryByText(/Реклама съедает маржу/)).not.toBeInTheDocument()
  })

  it('points a seller with no data at the ONLY path that actually works: uploading a report',
    async () => {
      // The Advisory MVP has exactly one way in — a report upload. There is no marketplace
      // connection UI anywhere in this app, and nothing syncs on its own. So the first-run
      // screen must send the seller to /dashboard/import and must not promise a sync.
      vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)

      render(<Dashboard />)

      const cta = await screen.findByRole('link', { name: /Загрузить отчёт/i })
      expect(cta).toHaveAttribute('href', '/dashboard/import')

      const body = document.body.textContent ?? ''
      expect(body).not.toMatch(/PULT подключён/)          // it is not connected to anything
      expect(body).not.toMatch(/синхронизаци/i)           // nothing synchronises
      expect(body).not.toMatch(/Проверить подключение/)   // that page does not exist
    })

  it('fails open to the normal dashboard if the summary call errors', async () => {
    vi.spyOn(api.today, 'getSummary').mockRejectedValue(new Error('boom'))

    render(<Dashboard />)

    // an active seller must never be trapped behind the first-run screen by a transient error
    await waitFor(() =>
      expect(screen.queryByText(/Нет данных для анализа/)).not.toBeInTheDocument())
  })
})

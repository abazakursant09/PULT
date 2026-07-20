import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Dashboard from '@/app/dashboard/page'
import { api } from '@/lib/api'
import { diagnosisCard, secondCard, todayNoData, todayWithData } from './fixtures'

// The dashboard used to decide whether to show the diagnosis feed from ONE flag:
// /api/today/summary.has_data — which is about the most recent day's MONEY, not about whether
// a diagnosis exists.
//
// So a seller could upload a report, PULT could diagnose a revenue collapse,
// /api/presentation/cards could return that card — and the dashboard would still say
// "Нет данных для анализа". The same summary reported critical_count: 1 in the very response
// that claimed there was no data. The browser smoke caught it.
//
// The rule is now the honest one: if a diagnosis exists, the seller sees it. `has_data` only
// gets a say when there is nothing to show.

const noCards = { cards: [] }
// Two cards: the dashboard passes skipTopAction, so the first is deliberately withheld
// from the feed (TodayFocus owns it). A lone card would prove nothing.
const withCards = { cards: [diagnosisCard, secondCard] }

describe('diagnosis visibility', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.setItem('token', 'test-token')
    // The dashboard and the TodayFocus panel it mounts fire several requests on mount. These two
    // were never stubbed and reached the real backend. Both fail quiet, so nothing below noticed
    // — the calls simply outlived the test and surfaced later with nothing to attribute them to.
    vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(null as never)
    vi.spyOn(api.today, 'get').mockResolvedValue({ top_action: null } as never)
  })

  it('shows the diagnosis even when today reports no data — the defect', async () => {
    // Exactly the state the browser smoke found: a real card, has_data=false.
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue(withCards)

    render(<Dashboard />)

    expect(await screen.findByText(/Остаток кончится/)).toBeInTheDocument()
    expect(screen.queryByText('Нет данных для анализа')).not.toBeInTheDocument()
  })

  it('shows the first-run screen only when there is genuinely nothing to show', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue(noCards)

    render(<Dashboard />)

    expect(await screen.findByText('Нет данных для анализа')).toBeInTheDocument()
    expect(screen.queryByText(/Остаток кончится/)).not.toBeInTheDocument()
  })

  it('keeps the dashboard for a seller with recent data but no diagnosis', async () => {
    // Nothing is wrong with this seller — there is simply nothing to diagnose today.
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayWithData)
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue(noCards)

    render(<Dashboard />)

    await waitFor(() =>
      expect(screen.queryByText('Нет данных для анализа')).not.toBeInTheDocument())
    expect(await screen.findByText('Состояние бизнеса сегодня')).toBeInTheDocument()
  })

  it('fails open when the summary errors — an active seller is never trapped', async () => {
    vi.spyOn(api.today, 'getSummary').mockRejectedValue(new Error('boom'))
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue(noCards)

    render(<Dashboard />)

    await waitFor(() =>
      expect(screen.queryByText('Нет данных для анализа')).not.toBeInTheDocument())
  })

  it('invents no diagnosis when the cards request fails', async () => {
    // We do not know whether a diagnosis exists, so we must not pretend one does.
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)
    vi.spyOn(api.presentation, 'getCards').mockRejectedValue(new Error('boom'))

    render(<Dashboard />)

    expect(await screen.findByText('Нет данных для анализа')).toBeInTheDocument()
    expect(screen.queryByText(/Остаток кончится/)).not.toBeInTheDocument()
  })

  it('never flashes the first-run screen at a seller who does have a diagnosis', async () => {
    // The summary answers instantly; the cards take a moment. Nothing may be drawn until both
    // have settled, or the seller sees "no data" and then a diagnosis appears underneath it.
    vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)
    vi.spyOn(api.presentation, 'getCards').mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(withCards), 50)))

    render(<Dashboard />)

    expect(screen.queryByText('Нет данных для анализа')).not.toBeInTheDocument()
    expect(await screen.findByText(/Остаток кончится/)).toBeInTheDocument()
    expect(screen.queryByText('Нет данных для анализа')).not.toBeInTheDocument()
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Dashboard from '@/app/dashboard/page'
import { api } from '@/lib/api'
import { diagnosisCard, firstRun, secondCard, todayNoData, todayWithData } from './fixtures'
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
    // The dashboard and the TodayFocus panel it mounts fire these on mount too. Three tests below
    // never stubbed them, so they reached the real backend: both fail quiet, so the assertions
    // passed regardless, and the calls outlived the tests that made them. Tests that care about
    // these values override them.
    vi.spyOn(api.today, 'get').mockResolvedValue({ top_action: null } as never)
    vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(null as never)
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
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })   // nothing to show

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
      vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })

      render(<Dashboard />)

      const cta = await screen.findByRole('link', { name: /Загрузить отчёт/i })
      expect(cta).toHaveAttribute('href', '/dashboard/import')

      const body = document.body.textContent ?? ''
      expect(body).not.toMatch(/PULT подключён/)          // it is not connected to anything
      expect(body).not.toMatch(/синхронизаци/i)           // nothing synchronises
      expect(body).not.toMatch(/Проверить подключение/)   // that page does not exist
    })

  // ── The first screen after an upload (B3) ──────────────────────────────────
  // A seller used to upload a report, be told "PULT анализирует", land here, and read
  // "Нет данных для анализа" — with the only button sending them back to the page they had
  // just come from. These pin the honest states.
  describe('what the seller is told before a diagnosis exists', () => {
    const noDiagnosis = () => {
      vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayNoData)
      vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })
    }

    it('does not say "нет данных" to a seller whose import is still being analysed', async () => {
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(firstRun({ state: 'analyzing' }))

      render(<Dashboard />)

      expect(await screen.findByText(/Разбор готовится/)).toBeInTheDocument()
      expect(screen.queryByText(/Нет данных для анализа/)).not.toBeInTheDocument()
    })

    it('offers no upload button while the analysis is running', async () => {
      // Sending them back to import is what made the old screen a loop.
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(firstRun({ state: 'analyzing' }))

      render(<Dashboard />)

      await screen.findByText(/Разбор готовится/)
      expect(screen.queryByRole('link', { name: /Загрузить отчёт/i })).not.toBeInTheDocument()
    })

    it('promises no completion time, because nothing guarantees one', async () => {
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(firstRun({ state: 'analyzing' }))

      render(<Dashboard />)

      await screen.findByText(/Разбор готовится/)
      const body = document.body.textContent ?? ''
      expect(body).not.toMatch(/через \d+ (минут|час|секунд)/i)
      expect(body).not.toMatch(/в течение \d+/i)
    })

    it('names the missing data instead of apologising in general terms', async () => {
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(firstRun({
        state: 'insufficient',
        finance_days: 2,
        missing: ['Для разбора выручки нужны данные минимум за 6 разных дней — сейчас 2.'],
      }))

      render(<Dashboard />)

      expect(await screen.findByText(/Данных пока недостаточно/)).toBeInTheDocument()
      expect(screen.getByText(/минимум за 6 разных дней/)).toBeInTheDocument()
      // …and it still offers the action that would actually help
      expect(screen.getByRole('link', { name: /Загрузить отчёт/i })).toBeInTheDocument()
    })

    it('does not blame the seller when the analysis itself failed', async () => {
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(firstRun({ state: 'failed' }))

      render(<Dashboard />)

      expect(await screen.findByText(/Разбор не удался/)).toBeInTheDocument()
      // Scoped to THIS screen: BusinessToday has its own "недостаточно данных за сегодня" line,
      // which is about today's money and is not the claim under test.
      expect(screen.queryByText(/Данных пока недостаточно для разбора/)).not.toBeInTheDocument()
    })

    it('falls back to the plain empty state if the status call fails', async () => {
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockRejectedValue(new Error('boom'))

      render(<Dashboard />)

      // No invented state: it says the one thing still known to be true.
      expect(await screen.findByText(/Нет данных для анализа/)).toBeInTheDocument()
    })

    it('promises CSV only — the backend rejects everything else', async () => {
      noDiagnosis()
      vi.spyOn(api.today, 'getFirstRun').mockResolvedValue(firstRun({ state: 'no_data' }))

      render(<Dashboard />)

      await screen.findByText(/Нет данных для анализа/)
      const body = document.body.textContent ?? ''
      expect(body).not.toMatch(/Excel/)
      expect(body).toMatch(/CSV/)
    })
  })

  it('fails open to the normal dashboard if the summary call errors', async () => {
    vi.spyOn(api.today, 'getSummary').mockRejectedValue(new Error('boom'))

    render(<Dashboard />)

    // an active seller must never be trapped behind the first-run screen by a transient error
    await waitFor(() =>
      expect(screen.queryByText(/Нет данных для анализа/)).not.toBeInTheDocument())
  })
})

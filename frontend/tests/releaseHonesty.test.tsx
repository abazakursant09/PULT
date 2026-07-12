import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ImportPage from '@/app/dashboard/import/page'
import NotificationsPage from '@/app/dashboard/notifications/page'
import { api } from '@/lib/api'
import type { ImportHistoryItem } from '@/lib/api'

// The three release blockers found by the readiness audit. Each of these tests fails the day
// someone puts one of them back.

const ROOT = join(__dirname, '..')

vi.mock('@/lib/analytics', () => ({
  trackEvent: vi.fn(),
  stampFunnel: vi.fn(),
  firstTimeOnly: () => false,
  elapsedSince: () => 0,
  FUNNEL_TS: { signup: 'signup', firstImport: 'firstImport' },
}))

function historyRow(import_type: string, status = 'confirmed'): ImportHistoryItem {
  return {
    id: 'imp-1', filename: 'r.csv', marketplace: 'wb', import_type, status,
    total_rows: 6, imported_count: 6, created_at: '2026-07-12T00:00:00Z',
    confirmed_at: '2026-07-12T00:00:00Z',
  }
}

describe('release blocker 1 — invented marketplace news is unreachable', () => {
  it('does not list Мониторинг in the seller navigation', () => {
    // Its "Проверить обновления" button promises "актуальные события с маркетплейсов" and
    // returns a random sample from a hard-coded pool of invented news — WB commission hikes,
    // a marking bill — three of them flagged critical. A seller can act on that.
    const shell = readFileSync(join(ROOT, 'components', 'seller', 'Shell.tsx'), 'utf-8')
    const code = shell.replace(/\/\/.*$/gm, '')          // ignore the comment explaining why

    expect(code).not.toMatch(/\/dashboard\/monitor/)
    expect(code).not.toMatch(/Мониторинг/)
  })
})

describe('release blocker 2 — notifications are never fabricated', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.notifications, 'list').mockResolvedValue({ items: [], total: 0, unread: 0 } as never)
    vi.spyOn(api.notifications, 'unreadCount').mockResolvedValue({ count: 0 } as never)
  })

  it('offers no way to seed demo notifications, and seeds none by itself', async () => {
    const seed = vi.spyOn(api.notifications, 'seed')

    render(<NotificationsPage />)

    expect(await screen.findByText('Уведомлений пока нет')).toBeInTheDocument()
    // A notification asserts that something happened to THIS seller. There is no honest way
    // to fabricate one, and once seeded the rows are indistinguishable from real ones.
    expect(screen.queryByText(/демо/i)).not.toBeInTheDocument()
    expect(seed).not.toHaveBeenCalled()
  })
})

describe('release blocker 3 — the first import must be the financial report', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.setItem('token', 'test-token')
  })

  it('explains why, and refuses to let Товары be the FIRST import', async () => {
    // The Advisory Runtime only considers a seller who has finance rows, so a first upload of
    // "Товары" would import cleanly and produce no diagnosis at all — and the dashboard would
    // then tell the seller to upload a report they had just uploaded.
    vi.spyOn(api.csvImport, 'history').mockResolvedValue([])

    render(<ImportPage />)

    expect(await screen.findByText(/Начните с финансового отчёта/)).toBeInTheDocument()

    // scope to the TYPE select — the marketplace select also has an "auto" option
    const type = screen.getByDisplayValue('Финансы') as HTMLSelectElement
    expect(type).toBeDisabled()
    expect(within(type).queryByRole('option', { name: 'Товары' })).not.toBeInTheDocument()
    expect(within(type).queryByRole('option', { name: 'Определить автоматически' }))
      .not.toBeInTheDocument()
  })

  it('restores the full choice once a financial report has been imported', async () => {
    vi.spyOn(api.csvImport, 'history').mockResolvedValue([historyRow('finance')])

    render(<ImportPage />)

    await waitFor(() =>
      expect(screen.queryByText(/Начните с финансового отчёта/)).not.toBeInTheDocument())
    const type = document.querySelectorAll('select')[1] as HTMLSelectElement   // ТИП ДАННЫХ
    expect(type).not.toBeDisabled()
    expect(within(type).getByRole('option', { name: 'Товары' })).toBeInTheDocument()
    expect(within(type).getByRole('option', { name: 'Определить автоматически' }))
      .toBeInTheDocument()
  })

  it('does not count an UNCONFIRMED finance import as having one', async () => {
    vi.spyOn(api.csvImport, 'history').mockResolvedValue([historyRow('finance', 'pending')])

    render(<ImportPage />)

    expect(await screen.findByText(/Начните с финансового отчёта/)).toBeInTheDocument()
  })

  it('fails OPEN — a broken history must not lock a seller out of importing', async () => {
    vi.spyOn(api.csvImport, 'history').mockRejectedValue(new Error('boom'))

    render(<ImportPage />)

    await waitFor(() =>
      expect(screen.queryByText(/Начните с финансового отчёта/)).not.toBeInTheDocument())
    const type = document.querySelectorAll('select')[1] as HTMLSelectElement
    expect(within(type).getByRole('option', { name: 'Товары' })).toBeInTheDocument()
  })
})

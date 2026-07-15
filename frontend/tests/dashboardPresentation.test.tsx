import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'
import BusinessToday from '@/components/dashboard/BusinessToday'
import { DecisionFeedEmptyState } from '@/components/decision-feed/DecisionFeedEmptyState'
import { severityBadgeVariant, severityLabel } from '@/lib/severity'
import { api } from '@/lib/api'
import { diagnosisCard, todayWithData } from './fixtures'

// P2 — dashboard + Decision Feed premium redesign. These lock the *presentation* promises the
// redesign makes, on top of the existing data-fidelity tests (BusinessToday / DecisionFeedPanel):
// severity is communicated by colour, loading shows a skeleton (no raw "Загрузка…" text, no
// layout shift), and the empty state stays honest. None of this touches data or API calls.

describe('P2 — severity presentation', () => {
  it('maps observed severity to a coloured badge variant, unknown → neutral', () => {
    expect(severityBadgeVariant('critical')).toBe('danger')
    expect(severityBadgeVariant('high')).toBe('warning')
    expect(severityBadgeVariant('medium')).toBe('neutral')
    expect(severityBadgeVariant('low')).toBe('neutral')
    expect(severityBadgeVariant('made-up')).toBe('neutral')
    expect(severityBadgeVariant(null)).toBe('neutral')
  })

  it('keeps the RU severity label verbatim, never invents one', () => {
    expect(severityLabel('critical')).toBe('Критично')
    expect(severityLabel('weird-code')).toBe('weird-code')
    expect(severityLabel(null)).toBeNull()
  })

  it('renders the severity label from a diagnosis card', async () => {
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [diagnosisCard] })
    render(<DecisionFeedPanel />)
    // diagnosisCard.highest_severity is a real class → its RU label shows on the header
    const expected = severityLabel(diagnosisCard.highest_severity)
    if (expected) expect(await screen.findByText(expected)).toBeInTheDocument()
  })
})

describe('P2 — loading is a skeleton, not raw text', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('BusinessToday shows a skeleton region while loading, no "Загрузка…" text', () => {
    // a promise that never resolves keeps the component in its loading state
    vi.spyOn(api.today, 'getSummary').mockReturnValue(new Promise(() => {}) as never)
    const { container } = render(<BusinessToday />)
    expect(container.querySelector('.pult-skeleton')).toBeTruthy()
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  })

  it('DecisionFeedPanel shows skeleton cards while loading', () => {
    vi.spyOn(api.presentation, 'getCards').mockReturnValue(new Promise(() => {}) as never)
    const { container } = render(<DecisionFeedPanel />)
    expect(container.querySelector('.pult-skeleton')).toBeTruthy()
  })
})

describe('P2 — honest empty state preserved', () => {
  it('feed empty state never claims all-clear', () => {
    render(<DecisionFeedEmptyState />)
    const bad = [/всё хорошо/i, /проблем нет/i, /в порядке/i]
    for (const re of bad) expect(screen.queryByText(re)).not.toBeInTheDocument()
    expect(screen.getByText(/PULT покажет решения здесь/)).toBeInTheDocument()
  })

  it('BusinessToday still states missing data plainly and invents no numbers', async () => {
    vi.spyOn(api.today, 'getSummary').mockResolvedValue({ ...todayWithData, has_data: false })
    render(<BusinessToday />)
    expect(await screen.findByText('Недостаточно данных за сегодня')).toBeInTheDocument()
    expect(screen.queryByText('Выручка')).not.toBeInTheDocument()
  })
})

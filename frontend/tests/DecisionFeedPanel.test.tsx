import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'
import { api } from '@/lib/api'
import { diagnosisCard } from './fixtures'

// The Decision Feed renders GET /api/presentation/cards — the diagnosis itself. This is the
// payload of the whole product: everything upstream (ingestion, 15 producers, 8 contours)
// exists so that these cards appear. If they silently stop rendering, PULT shows a seller
// nothing at all while every backend test still passes.

describe('DecisionFeedPanel (presentation cards)', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('renders a diagnosis card from the API', async () => {
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [diagnosisCard] })

    render(<DecisionFeedPanel />)

    // the card's own diagnosis text, verbatim from the response
    expect(await screen.findByText(/Реклама съедает маржу/)).toBeInTheDocument()
    // the SKU appears on the card header and again on the nested item — both are the product
    expect(screen.getAllByText(/ART-1001/).length).toBeGreaterThan(0)
  })

  it('asks the backend for cards rather than deriving them client-side', async () => {
    const spy = vi.spyOn(api.presentation, 'getCards')
      .mockResolvedValue({ cards: [diagnosisCard] })

    render(<DecisionFeedPanel />)

    await waitFor(() => expect(spy).toHaveBeenCalled())
    // grouping/dedup/ordering is a server concern (P0–P3). The client must not re-derive it.
    expect(spy.mock.calls[0][0]).toMatchObject({ limit: 50 })
  })

  it('shows no diagnosis when the backend returns none — and fabricates nothing', async () => {
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })

    render(<DecisionFeedPanel />)

    await waitFor(() =>
      expect(screen.queryByText(/Реклама съедает маржу/)).not.toBeInTheDocument())
    expect(screen.queryByText(/ART-1001/)).not.toBeInTheDocument()
  })

  it('does not crash when the feed fails to load', async () => {
    vi.spyOn(api.presentation, 'getCards').mockRejectedValue(new Error('boom'))

    render(<DecisionFeedPanel />)

    await waitFor(() =>
      expect(screen.queryByText(/Реклама съедает маржу/)).not.toBeInTheDocument())
  })
})

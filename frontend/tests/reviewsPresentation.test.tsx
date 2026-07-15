import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewsPage from '@/app/dashboard/reviews/page'
import { STATE_BADGE_VARIANT, STATE_LABEL } from '@/lib/reviewState'
import { api, type ReviewResponse } from '@/lib/api'

// P3 — reviews workspace premium redesign. Locks the *presentation* promises on top of the existing
// behaviour tests (reviewWorkspace): status is a coloured badge, loading is a skeleton (no raw
// "Загрузка…"), the empty state stays honest, and safety is shown for every review. No data/API change.

function review(over: Partial<ReviewResponse> = {}): ReviewResponse {
  return {
    id: 'r1', product_id: 'p1', review_text: 'отличный товар', author: 'Иван', rating: 5,
    response_text: null, status: 'pending', marketplace: 'wildberries', external_review_id: 'WB-1',
    review_created_at: null, safety_category: 'SAFE', manual_required_reason: null,
    published_at: null, failure_reason: null, publication_attempts: 0,
    created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z', state: 'New',
    ...over,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.reviews, 'history').mockResolvedValue({ review_id: 'r1', entries: [] } as never)
})

describe('P3 — review state presentation', () => {
  it('maps every lifecycle state to a semantic badge variant', () => {
    expect(STATE_BADGE_VARIANT.Failed).toBe('danger')
    expect(STATE_BADGE_VARIANT.NeedsAttention).toBe('warning')
    expect(STATE_BADGE_VARIANT.Approved).toBe('success')
    expect(STATE_BADGE_VARIANT.Published).toBe('success')
    expect(STATE_BADGE_VARIANT.Drafted).toBe('default')
    expect(STATE_BADGE_VARIANT.New).toBe('neutral')
    // every state has a RU label and a variant — no state falls through to undefined
    for (const s of Object.keys(STATE_LABEL) as (keyof typeof STATE_LABEL)[]) {
      expect(STATE_BADGE_VARIANT[s]).toBeTruthy()
    }
  })
})

describe('P3 — loading is a skeleton, not raw text', () => {
  it('shows skeletons while the queue loads, no "Загрузка…" text', () => {
    vi.spyOn(api.reviews, 'queue').mockReturnValue(new Promise(() => {}) as never)
    const { container } = render(<ReviewsPage />)
    expect(container.querySelector('.pult-skeleton')).toBeTruthy()
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument()
  })
})

describe('P3 — honest empty state', () => {
  it('renders the canonical empty state and never claims demo data', async () => {
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 } as never)
    render(<ReviewsPage />)
    expect(await screen.findByText('Отзывов пока нет')).toBeInTheDocument()
    expect(screen.getByText(/Демо-данные не показываются/)).toBeInTheDocument()
  })
})

describe('P3 — safety is shown for every review', () => {
  it('confirms a SAFE review as safe for auto-answer', async () => {
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({
      items: [review({ safety_category: 'SAFE' })], total: 1, limit: 50, offset: 0,
    } as never)
    const user = userEvent.setup()
    render(<ReviewsPage />)
    await user.click(await screen.findByText('отличный товар'))
    expect(await screen.findByText('Безопасно для автоответа')).toBeInTheDocument()
  })

  it('shows the human-review reason for a non-SAFE review', async () => {
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({
      items: [review({ safety_category: 'RISK', state: 'NeedsAttention', manual_required_reason: 'жалоба на товар' })],
      total: 1, limit: 50, offset: 0,
    } as never)
    const user = userEvent.setup()
    render(<ReviewsPage />)
    await user.click(await screen.findByText('отличный товар'))
    await waitFor(() => expect(screen.getByText('жалоба на товар')).toBeInTheDocument())
  })
})

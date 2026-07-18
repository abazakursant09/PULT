import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReviewsPage from '@/app/dashboard/reviews/page'
import { api, type ReviewResponse } from '@/lib/api'

// The AR3 seller review workspace: renders real reviews with their derived state, shows an honest
// empty state, and its buttons call the correct review APIs.

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

describe('review workspace', () => {
  it('shows an honest empty state, not demo data', async () => {
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 } as never)
    render(<ReviewsPage />)
    expect(await screen.findByText('Отзывов пока нет')).toBeInTheDocument()
    expect(screen.getByText(/Демо-данные не показываются/)).toBeInTheDocument()
  })

  it('renders reviews with their state badge', async () => {
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({
      items: [review({ state: 'NeedsAttention', safety_category: 'RISK', rating: 1,
                       manual_required_reason: 'жалоба' })],
      total: 1, limit: 50, offset: 0,
    } as never)
    render(<ReviewsPage />)
    expect(await screen.findByText('Требует внимания')).toBeInTheDocument()
    expect(screen.getByText('отличный товар')).toBeInTheDocument()
  })

  it('draft / approve / publish buttons call the matching APIs', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({
      items: [review()], total: 1, limit: 50, offset: 0,
    } as never)
    const draft = vi.spyOn(api.reviews, 'draft').mockResolvedValue(review({ state: 'Drafted', status: 'drafted', response_text: 'Спасибо!' }) as never)
    const approve = vi.spyOn(api.reviews, 'approve').mockResolvedValue(review({ state: 'Approved', status: 'approved', response_text: 'Спасибо!' }) as never)
    const publish = vi.spyOn(api.reviews, 'publish').mockResolvedValue(review({ state: 'Published', status: 'published' }) as never)

    render(<ReviewsPage />)
    // open the review in the detail pane
    await user.click(await screen.findByText('отличный товар'))

    await user.click(screen.getByRole('button', { name: 'Создать черновик' }))
    await waitFor(() => expect(draft).toHaveBeenCalledWith('p1', 'r1'))
    await user.click(screen.getByRole('button', { name: 'Одобрить' }))
    await waitFor(() => expect(approve).toHaveBeenCalledWith('p1', 'r1'))
    await user.click(screen.getByRole('button', { name: 'Опубликовать' }))
    await waitFor(() => expect(publish).toHaveBeenCalledWith('p1', 'r1'))
  })

  it('surfaces a failed publication', async () => {
    vi.spyOn(api.reviews, 'queue').mockResolvedValue({
      items: [review({ state: 'Failed', failure_reason: 'TIMEOUT', publication_attempts: 2 })],
      total: 1, limit: 50, offset: 0,
    } as never)
    const user = userEvent.setup()
    render(<ReviewsPage />)
    await user.click(await screen.findByText('отличный товар'))
    // AR-VIS-2: the failure is surfaced, but the stored failure_reason is NOT — it carries internal
    // codes and the marketplace's raw response body. A safe, translated cause is AR-VIS-3.
    expect(screen.getByText('Опубликовать не удалось. Попыток: 2')).toBeInTheDocument()
    expect(document.body.textContent || '').not.toMatch(/TIMEOUT/)
  })
})

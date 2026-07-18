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
  // The page embeds AutoReviewsPanel, which fetches on mount. Stub those so no test reaches the
  // network — an unmocked call escapes as an unhandled rejection and can mask a real failure.
  vi.spyOn(api.connections, 'list').mockResolvedValue([] as never)
  vi.spyOn(api.automation, 'availability').mockResolvedValue({ automation_enabled: false } as never)
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

// ── AR-VIS-2: the fate of a reply, in the seller's language ─────────────────────────────────────
// The seller must understand what happened to each reply without meeting a single internal code, and
// must never be told something the backend did not record.

/** A backend timestamp: naive UTC with no suffix — exactly what FastAPI emits. */
const utcStamp = (d: Date): string => d.toISOString().replace('Z', '')

const openReview = async (over: Partial<ReviewResponse>, entries: unknown[] = []) => {
  vi.spyOn(api.reviews, 'queue').mockResolvedValue({
    items: [review(over)], total: 1, limit: 50, offset: 0,
  } as never)
  vi.spyOn(api.reviews, 'history').mockResolvedValue({ review_id: 'r1', entries } as never)
  const user = userEvent.setup()
  render(<ReviewsPage />)
  await user.click(await screen.findByText('отличный товар'))
}

describe('AR-VIS-2 — the fate of the reply', () => {
  it('a published reply states it was published, with the date', async () => {
    const at = new Date(Date.now() - 3600_000)
    await openReview({ state: 'Published', status: 'published', published_at: utcStamp(at) })
    expect(await screen.findByText(/Ответ опубликован/)).toBeInTheDocument()
  })

  it('an approved reply says it is waiting to be published', async () => {
    await openReview({ state: 'Approved', status: 'approved', response_text: 'спасибо' })
    expect(await screen.findByText('Ответ одобрен и ждёт публикации')).toBeInTheDocument()
  })

  it('a drafted reply asks the seller to confirm', async () => {
    await openReview({ state: 'Drafted', status: 'drafted', response_text: 'спасибо' })
    expect(await screen.findByText('Черновик готов — нужно ваше подтверждение')).toBeInTheDocument()
  })

  it('a review left to a human says so', async () => {
    await openReview({ state: 'NeedsAttention', safety_category: 'RISK' })
    expect(await screen.findByText('Этот отзыв отвечается вручную')).toBeInTheDocument()
  })

  it('a failed reply reports the failure and the attempt count, nothing more', async () => {
    await openReview({
      state: 'Failed', status: 'failed', publication_attempts: 2,
      failure_reason: 'MARKETPLACE_4XX: 404: {"code":"NOT_FOUND"}',
    })
    expect(await screen.findByText('Опубликовать не удалось. Попыток: 2')).toBeInTheDocument()
  })

  it('an untouched review says no reply was prepared yet', async () => {
    await openReview({ state: 'New' })
    expect(await screen.findByText('Ответ ещё не готовился')).toBeInTheDocument()
  })
})

describe('AR-VIS-2 — publication history in plain Russian', () => {
  const attempt = (over: Record<string, unknown> = {}) => ({
    timestamp: utcStamp(new Date(Date.now() - 7200_000)),
    mode: 'automated_l4', status: 'success', error_code: null, ...over,
  })

  it('an automatic publish is named as automatic', async () => {
    await openReview({ state: 'Published' }, [attempt()])
    expect(await screen.findByText(/опубликовано автоматически/)).toBeInTheDocument()
  })

  it('a manual publish is named as manual', async () => {
    await openReview({ state: 'Published' }, [attempt({ mode: 'manual_l3' })])
    expect(await screen.findByText(/опубликовано вручную/)).toBeInTheDocument()
  })

  it('a failed attempt says the attempt failed', async () => {
    await openReview({ state: 'Failed' }, [attempt({ status: 'failed', error_code: 'MARKETPLACE_4XX' })])
    expect(await screen.findByText(/попытка не удалась/)).toBeInTheDocument()
  })

  it('an ambiguous attempt tells the seller to check the marketplace cabinet', async () => {
    await openReview({ state: 'Failed' }, [attempt({ status: 'ambiguous' })])
    expect(await screen.findByText(/результат не подтверждён, проверьте кабинет маркетплейса/)).toBeInTheDocument()
  })

  it('a rejected attempt says PULT itself stopped it', async () => {
    await openReview({ state: 'New' }, [attempt({ status: 'rejected', error_code: 'GUARD_NEGATIVE' })])
    expect(await screen.findByText(/публикация отклонена проверками PULT/)).toBeInTheDocument()
  })

  it('an in-flight attempt says it is running', async () => {
    await openReview({ state: 'New' }, [attempt({ status: 'pending' })])
    expect(await screen.findByText(/попытка выполняется/)).toBeInTheDocument()
  })

  it('an unknown status degrades to a neutral line instead of inventing meaning', async () => {
    await openReview({ state: 'New' }, [attempt({ status: 'something_new' })])
    expect(await screen.findByText(/попытка завершена/)).toBeInTheDocument()
  })

  it('with no attempts, says there were none', async () => {
    await openReview({ state: 'New' }, [])
    expect(await screen.findByText('Публикаций ещё не было.')).toBeInTheDocument()
  })
})

describe('AR-VIS-2 — nothing internal reaches the screen', () => {
  it('never shows raw modes, statuses, error codes or the stored failure text', async () => {
    await openReview(
      {
        state: 'Failed', status: 'failed', publication_attempts: 3,
        failure_reason: 'MARKETPLACE_4XX: 404: {"message":"feedback not found"}',
      },
      [
        { timestamp: utcStamp(new Date()), mode: 'automated_l4', status: 'failed', error_code: 'MARKETPLACE_4XX' },
        { timestamp: utcStamp(new Date()), mode: 'manual_l3', status: 'success', error_code: null },
      ],
    )
    await screen.findByText('Опубликовать не удалось. Попыток: 3')
    const shown = document.body.textContent || ''
    expect(shown).not.toMatch(/automated_l4|manual_l3|MARKETPLACE_4XX|feedback not found|ambiguous|rejected/)
  })

  it('never labels a draft as automatic — who wrote it is not stored', async () => {
    await openReview({ state: 'Drafted', status: 'drafted', response_text: 'спасибо' }, [])
    await screen.findByText('Черновик готов — нужно ваше подтверждение')
    expect(document.body.textContent || '').not.toMatch(/автоматически/)
  })

  it('renders a suffix-less backend timestamp in the browser timezone, not as raw UTC', async () => {
    const at = new Date(Date.now() - 5 * 3600_000)
    at.setUTCSeconds(0, 0)
    await openReview({ state: 'Published' }, [
      { timestamp: utcStamp(at), mode: 'automated_l4', status: 'success', error_code: null },
    ])
    const local = at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    expect(await screen.findByText(new RegExp(`${local} — опубликовано автоматически`))).toBeInTheDocument()
    // the raw ISO form must not leak through
    expect(document.body.textContent || '').not.toMatch(/\d{4}-\d{2}-\d{2}T/)
  })
})

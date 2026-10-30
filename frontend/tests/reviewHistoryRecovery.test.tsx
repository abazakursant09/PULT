import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReviewsPage from '@/app/dashboard/reviews/page'
import { api, type ReviewResponse } from '@/lib/api'

// Same review fixture and embedded-panel API mocks as reviewsPresentation.
function review(id: string): ReviewResponse {
  return {
    id, product_id: `p-${id}`, review_text: `Review ${id}`, author: 'Иван', rating: 5,
    response_text: null, status: 'pending', marketplace: 'wildberries', external_review_id: id,
    review_created_at: null, safety_category: 'SAFE', manual_required_reason: null,
    published_at: null, failure_reason: null, publication_attempts: 0,
    created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z', state: 'New',
  }
}
type History = Awaited<ReturnType<typeof api.reviews.history>>
function deferred<T = History>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const empty = { review_id: 'A', entries: [] } as unknown as History
const populated = (mode = 'manual_l3') => ({ review_id: 'A', entries: [
  { timestamp: null, mode, status: 'success', error_code: null },
] }) as unknown as History
const select = (id: string) => fireEvent.click(screen.getByRole('button', { name: new RegExp(`Review ${id}`) }))
const retry = () => screen.getByRole('button', { name: 'Повторить загрузку истории' })
const noEmpty = () => expect(screen.queryByText('Публикаций ещё не было.')).not.toBeInTheDocument()

beforeEach(() => {
  vi.spyOn(api.reviews, 'queue').mockResolvedValue({ items: [review('A'), review('B')], total: 2, limit: 50, offset: 0 } as never)
  vi.spyOn(api.reviews, 'history').mockResolvedValue(empty)
  vi.spyOn(api.connections, 'list').mockResolvedValue([] as never)
  vi.spyOn(api.automation, 'availability').mockResolvedValue({ automation_enabled: false } as never)
  for (const method of ['draft', 'update', 'approve', 'publish'] as const) {
    vi.spyOn(api.reviews, method).mockResolvedValue(review('A'))
  }
})

type Queue = Awaited<ReturnType<typeof api.reviews.queue>>
const queueResult = (items: ReviewResponse[] = []) => ({ items, total: items.length, limit: 50, offset: 0 }) as Queue
const queueRetry = () => screen.getByRole('button', { name: 'Повторить загрузку отзывов' })
const noQueueEmpty = () => expect(screen.queryByText('Отзывов пока нет')).not.toBeInTheDocument()
const noReviewMutations = () => {
  for (const method of ['draft', 'update', 'approve', 'publish'] as const) expect(api.reviews[method]).not.toHaveBeenCalled()
}

describe('review queue read recovery', () => {
  it('reports queue failure without claiming an empty queue', async () => {
    vi.mocked(api.reviews.queue).mockRejectedValue(new Error('HTTP 500 internal'))
    render(<ReviewsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить отзывы')
    expect(queueRetry()).toBeEnabled()
    noQueueEmpty()
    noReviewMutations()
  })

  for (const hasItems of [false, true]) {
    it(`retries GET with current filter and recovers to ${hasItems ? 'data' : 'empty'}`, async () => {
      vi.mocked(api.reviews.queue).mockRejectedValue(new Error('HTTP 500'))
      render(<ReviewsPage />)
      await act(async () => {})
      fireEvent.click(screen.getByRole('button', { name: 'Новый', exact: true }))
      await screen.findByRole('alert')
      const pending = deferred<Queue>()
      vi.mocked(api.reviews.queue).mockReturnValueOnce(pending.promise)
      fireEvent.click(queueRetry())
      noQueueEmpty()
      expect(screen.getByLabelText('Загрузка')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Повторить загрузку отзывов' })).not.toBeInTheDocument()
      expect(api.reviews.queue).toHaveBeenLastCalledWith({ state: 'New' })
      await act(async () => pending.resolve(queueResult(hasItems ? [review('A')] : [])))
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      if (hasItems) expect(screen.getByText('Review A')).toBeInTheDocument()
      else expect(screen.getByText('Отзывов пока нет')).toBeInTheDocument()
      expect(api.reviews.history).not.toHaveBeenCalled()
      noReviewMutations()
    })
  }

  it('keeps repeated queue failure recoverable', async () => {
    vi.mocked(api.reviews.queue).mockRejectedValue(new Error('HTTP 500'))
    render(<ReviewsPage />)
    await screen.findByRole('alert')
    fireEvent.click(queueRetry())
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить отзывы')
    expect(api.reviews.queue).toHaveBeenCalledTimes(2)
    expect(queueRetry()).toBeEnabled()
    noQueueEmpty()
    noReviewMutations()
  })

  for (const outcome of ['success', 'rejection'] as const) {
    it(`ignores stale filter ${outcome} after All→New→All`, async () => {
      const first = deferred<Queue>(), second = deferred<Queue>(), latest = deferred<Queue>()
      vi.mocked(api.reviews.queue).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(latest.promise)
      render(<ReviewsPage />)
      fireEvent.click(screen.getByRole('button', { name: 'Новый', exact: true }))
      fireEvent.click(screen.getByRole('button', { name: 'Все', exact: true }))
      await act(async () => latest.resolve(queueResult([review('A')])))
      await act(async () => {
        if (outcome === 'success') first.resolve(queueResult([review('STALE')]))
        else first.reject(new Error('stale queue failure'))
        second.resolve(queueResult([]))
      })
      expect(screen.getByText('Review A')).toBeInTheDocument()
      expect(screen.queryByText('Review STALE')).not.toBeInTheDocument()
      expect(screen.queryByText('stale queue failure')).not.toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      noQueueEmpty()
    })
  }
})
afterEach(() => vi.restoreAllMocks())
async function mount() {
  const view = render(<ReviewsPage />)
  await screen.findByText('Review A')
  return view
}

describe('review history read recovery', () => {
  it('shows empty only after successful GET', async () => {
    const pending = deferred()
    vi.mocked(api.reviews.history).mockReturnValue(pending.promise)
    await mount(); select('A')
    noEmpty()
    expect(screen.getByLabelText('Загрузка истории публикаций')).toBeInTheDocument()
    await act(async () => pending.resolve(empty))
    expect(screen.getByText('Публикаций ещё не было.')).toBeInTheDocument()
  })

  it('renders a separate failure and GET-only retry with exclusive pending and populated states', async () => {
    vi.mocked(api.reviews.history).mockRejectedValueOnce(new Error('500 internal body'))
    await mount(); select('A')
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить историю публикаций')
    noEmpty()
    expect(screen.queryByText(/500 internal body/)).not.toBeInTheDocument()
    const pending = deferred()
    vi.mocked(api.reviews.history).mockReturnValueOnce(pending.promise)
    fireEvent.click(retry())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить загрузку истории' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Загрузка истории публикаций')).toBeInTheDocument()
    noEmpty()
    await act(async () => pending.resolve(populated()))
    expect(screen.getByText('опубликовано вручную')).toBeInTheDocument()
    expect(screen.queryByLabelText('Загрузка истории публикаций')).not.toBeInTheDocument()
    expect(api.reviews.history).toHaveBeenNthCalledWith(2, 'p-A', 'A')
    for (const method of ['draft', 'update', 'approve', 'publish'] as const) expect(api.reviews[method]).not.toHaveBeenCalled()
  })

  for (const revisit of [false, true]) {
    for (const outcome of ['success', 'rejection'] as const) {
      it(`ignores stale ${outcome} after A→B${revisit ? '→A' : ''}`, async () => {
        const first = deferred(), second = deferred(), third = deferred()
        vi.mocked(api.reviews.history)
          .mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(third.promise)
        await mount(); select('A'); select('B')
        if (revisit) select('A')
        const latest = revisit ? third : second
        await act(async () => latest.resolve(populated()))
        expect(screen.getByText('опубликовано вручную')).toBeInTheDocument()
        await act(async () => {
          if (outcome === 'success') first.resolve(populated('automated_l4'))
          else first.reject(new Error('old failure'))
          if (revisit) second.reject(new Error('old B failure'))
        })
        expect(screen.getByText('опубликовано вручную')).toBeInTheDocument()
        expect(screen.queryByText('опубликовано автоматически')).not.toBeInTheDocument()
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
        noEmpty()
      })
    }
  }

  it('does not let stale success clear the latest failure', async () => {
    const first = deferred(), second = deferred()
    vi.mocked(api.reviews.history).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    await mount(); select('A'); select('B')
    await act(async () => second.reject(new Error('latest failure')))
    await screen.findByRole('alert')
    await act(async () => first.resolve(populated()))
    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось загрузить историю публикаций')
    expect(retry()).toBeEnabled()
    expect(screen.queryByText('опубликовано вручную')).not.toBeInTheDocument()
  })

  it('settles old requests safely after unmount without affecting a new instance', async () => {
    const old = deferred()
    vi.mocked(api.reviews.history).mockReturnValueOnce(old.promise)
    const view = await mount(); select('A'); view.unmount()
    await mount(); select('B')
    await screen.findByText('Публикаций ещё не было.')
    await act(async () => old.reject(new Error('unmounted failure')))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Публикаций ещё не было.')).toBeInTheDocument()
  })
})

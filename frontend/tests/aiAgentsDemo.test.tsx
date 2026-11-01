import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AIAgentsPage from '@/app/ai-agents/page'

vi.mock('@/components/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }))

const pricingReply = 'Демо-сценарий цены: сравните исходные данные и варианты решения. Это пример диалога, а не расчёт цены или рекомендация для вашего магазина.'
const reviewsReply = 'Демо-сценарий отзыва: сначала уточните вопрос покупателя, затем подготовьте черновик ответа. Текст не отправляется покупателю.'
const scrollDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
const input = () => screen.getByPlaceholderText(/Спросить у агента/)
const send = (text: string) => {
  fireEvent.change(input(), { target: { value: text } })
  fireEvent.keyDown(input(), { key: 'Enter' })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Demo must not use transport')))
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true, writable: true })
})
afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (scrollDescriptor) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', scrollDescriptor)
  else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
})

it('discloses demo/no AI, provider or store connection and local-only nonpersistent text before input', () => {
  render(<AIAgentsPage />)
  const note = screen.getByRole('note', { name: 'Демонстрационный режим' })
  expect(note).toHaveTextContent('ДЕМО')
  expect(note).toHaveTextContent('Данные магазина, ИИ и внешние сервисы не подключены.')
  expect(note).toHaveTextContent('Введённый текст остаётся только в памяти этой страницы, не сохраняется и не отправляется.')
  expect(fetch).not.toHaveBeenCalled()
})

it('returns the same clearly illustrative reply without randomness, transport or persisted input', () => {
  const random = vi.spyOn(Math, 'random')
  const write = vi.spyOn(Storage.prototype, 'setItem')
  const first = render(<AIAgentsPage />)
  send('Synthetic local input one')
  act(() => { vi.advanceTimersByTime(1200) })
  expect(screen.getByText(pricingReply)).toBeInTheDocument()
  send('Synthetic unrelated input two')
  act(() => { vi.advanceTimersByTime(1200) })
  expect(screen.getAllByText(pricingReply)).toHaveLength(2)
  expect(random).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
  expect(write).not.toHaveBeenCalled()
  first.unmount()
  render(<AIAgentsPage />)
  expect(screen.queryByText('Synthetic local input one')).not.toBeInTheDocument()
  expect(screen.queryByText(pricingReply)).not.toBeInTheDocument()
})

it('uses sample wording for every agent instead of current-market or customer analysis claims', () => {
  render(<AIAgentsPage />)
  for (const name of ['Ценообразование', 'Отзывы', 'Юридический', 'Товары', 'Финансы', 'Реклама']) {
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${name}`) }))
    expect(document.body.textContent).not.toMatch(/По данным на сегодня|медианная цена|1 240|1 490|1 190|22%|Анализирую конкурентов|Отслеживаю изменения|Проанализирую ваши|Разберём финансовые показатели вашего магазина/)
    expect(screen.getByRole('note', { name: 'Демонстрационный режим' })).toBeVisible()
    send('Synthetic sample question')
    act(() => { vi.advanceTimersByTime(1200) })
    expect(document.body.textContent).not.toMatch(/По данным на сегодня|медианная цена|1 240|1 490|1 190|22%/)
  }
  expect(fetch).not.toHaveBeenCalled()
})

it('cancels an old pending reply on agent switch and permits the new agent to reply', () => {
  render(<AIAgentsPage />)
  send('Synthetic pricing request')
  expect(vi.getTimerCount()).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: /^Отзывы/ }))
  expect(vi.getTimerCount()).toBe(0)
  send('Synthetic review request')
  act(() => { vi.advanceTimersByTime(1200) })
  expect(screen.getByText(reviewsReply)).toBeInTheDocument()
  expect(screen.queryByText(pricingReply)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /^Ценообразование/ }))
  expect(screen.getByText('Synthetic pricing request')).toBeInTheDocument()
  expect(screen.queryByText(reviewsReply)).not.toBeInTheDocument()
  expect(screen.queryByText(pricingReply)).not.toBeInTheDocument()
  expect(fetch).not.toHaveBeenCalled()
})

it('clears the pending timer on unmount rather than updating an abandoned page', () => {
  const view = render(<AIAgentsPage />)
  send('Synthetic abandoned request')
  expect(vi.getTimerCount()).toBe(1)
  view.unmount()
  expect(vi.getTimerCount()).toBe(0)
  act(() => { vi.advanceTimersByTime(1200) })
  expect(fetch).not.toHaveBeenCalled()
})

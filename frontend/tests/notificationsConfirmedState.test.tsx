import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import NotificationsPage from '@/app/dashboard/notifications/page'
import { api, type NotificationsResponse } from '@/lib/api'

vi.mock('@/components/ui/blur-fade', () => ({ BlurFade: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }))

const data = (): NotificationsResponse => ({ items: [{ id: 'fixture-1', type: 'new_review', title: 'Тестовое уведомление', message: 'Синтетическое событие', is_read: false, created_at: '2026-10-01T12:00:00Z' }], total: 20, unread_count: 2, page: 1, per_page: 10 })
beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.notifications, 'list').mockResolvedValue(data())
  vi.spyOn(api.notifications, 'markRead').mockResolvedValue({ ok: true })
  vi.spyOn(api.notifications, 'markAllRead').mockResolvedValue({ ok: true })
})

it('distinguishes a failed list from a confirmed empty inbox and offers retry', async () => {
  vi.mocked(api.notifications.list).mockRejectedValueOnce(new Error('offline'))
  render(<NotificationsPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить уведомления')
  expect(screen.queryByText('Уведомлений пока нет')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  expect(await screen.findByText('Тестовое уведомление')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('shows empty only after a successful empty response', async () => {
  vi.mocked(api.notifications.list).mockResolvedValue({ ...data(), items: [], total: 0, unread_count: 0 })
  render(<NotificationsPage />)
  expect(await screen.findByText('Уведомлений пока нет')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('preserves unread items and counters after a rejected single read', async () => {
  vi.mocked(api.notifications.markRead).mockRejectedValue(new Error('offline'))
  render(<NotificationsPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Прочитать', exact: true }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось отметить уведомление')
  expect(screen.getByRole('button', { name: 'Прочитать', exact: true })).toBeEnabled()
  expect(screen.getByText('2 непрочитанных')).toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('does not treat ok:false as a confirmed read', async () => {
  vi.mocked(api.notifications.markRead).mockResolvedValue({ ok: false })
  render(<NotificationsPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Прочитать', exact: true }))
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(screen.getByText('2 непрочитанных')).toBeInTheDocument()
})

it('waits for confirmation, prevents duplicate actions, then decrements once', async () => {
  let resolve!: (value: { ok: boolean }) => void
  vi.mocked(api.notifications.markRead).mockReturnValue(new Promise(done => { resolve = done }))
  render(<NotificationsPage />)
  const read = await screen.findByRole('button', { name: 'Прочитать', exact: true })
  fireEvent.click(read); fireEvent.click(read)
  expect(api.notifications.markRead).toHaveBeenCalledTimes(1)
  expect(read).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Прочитать все' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Следующая страница' })).toBeDisabled()
  expect(screen.getByText('2 непрочитанных')).toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  await act(async () => resolve({ ok: true }))
  expect(await screen.findByRole('status')).toHaveTextContent('Уведомление отмечено прочитанным')
  expect(screen.getByText('1 непрочитанных')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Прочитать', exact: true })).not.toBeInTheDocument()
})

it('preserves the inbox after failed mark-all and confirms only a successful retry', async () => {
  vi.mocked(api.notifications.markAllRead).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ok: true })
  render(<NotificationsPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Прочитать все' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось отметить все уведомления')
  expect(screen.getByText('2 непрочитанных')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Прочитать', exact: true })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Прочитать все' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Все уведомления отмечены прочитанными')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Прочитать', exact: true })).not.toBeInTheDocument()
})

it('names pagination and does not present stale page data after a failed page change', async () => {
  vi.mocked(api.notifications.list).mockResolvedValueOnce(data()).mockRejectedValueOnce(new Error('offline'))
  render(<NotificationsPage />)
  expect(await screen.findByText('Тестовое уведомление')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Предыдущая страница' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
  await waitFor(() => expect(api.notifications.list).toHaveBeenLastCalledWith(2, 10))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить уведомления')
  expect(screen.queryByText('Тестовое уведомление')).not.toBeInTheDocument()
  expect(screen.queryByText('Уведомлений пока нет')).not.toBeInTheDocument()
})

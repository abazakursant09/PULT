import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import Page from '@/app/admin/promocodes/page'
import { api, type PromoEntry, type PromoStats } from '@/lib/api'

vi.mock('@/lib/api', () => ({ api: { promo: {
  adminList: vi.fn(), adminStats: vi.fn(), adminCreate: vi.fn(), adminToggle: vi.fn(),
} } }))

const promo: PromoEntry = {
  id: 'synthetic', code: 'TESTONLY', type: 'percent', value: 12,
  description: null, applicable_plans: 'all', max_activations: null,
  current_activations: 3, is_active: true, blogger_name: null,
  expires_at: null, created_at: '2026-11-01',
}
const stats: PromoStats = { total_promos: 1, active_promos: 1, total_activations: 3, bloggers: [] }
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.promo.adminList).mockResolvedValue([promo])
  vi.mocked(api.promo.adminStats).mockResolvedValue(stats)
  vi.mocked(api.promo.adminToggle).mockResolvedValue(promo)
  vi.mocked(api.promo.adminCreate).mockResolvedValue(promo)
})

it.each(['adminList', 'adminStats'] as const)('does not present failed %s as an empty success; retry only reads', async method => {
  vi.mocked(api.promo[method]).mockRejectedValueOnce(new Error('403 internal access required'))
  render(<Page />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить промокоды и статистику')
  expect(screen.queryByText('Нет промокодов')).not.toBeInTheDocument()
  expect(screen.queryByText('Всего промокодов')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Все (0)' })).not.toBeInTheDocument()
  expect(screen.queryByTitle('Деактивировать')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Создать промокод' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  expect(await screen.findByText('TESTONLY')).toBeInTheDocument()
  expect(api.promo.adminList).toHaveBeenCalledTimes(2)
  expect(api.promo.adminStats).toHaveBeenCalledTimes(2)
  expect(api.promo.adminCreate).not.toHaveBeenCalled()
  expect(api.promo.adminToggle).not.toHaveBeenCalled()
})

it('hides stale rows/stats during and after a failed post-toggle read; retry never replays PATCH', async () => {
  const refresh = deferred<PromoEntry[]>()
  vi.mocked(api.promo.adminList).mockResolvedValueOnce([promo]).mockReturnValueOnce(refresh.promise)
  render(<Page />)
  await screen.findByText('TESTONLY')
  fireEvent.click(screen.getByTitle('Деактивировать'))
  await waitFor(() => expect(api.promo.adminList).toHaveBeenCalledTimes(2))
  expect(screen.queryByText('TESTONLY')).not.toBeInTheDocument()
  expect(screen.queryByText('Всего промокодов')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Создать промокод' })).toBeDisabled()
  await act(async () => refresh.reject(new Error('offline')))
  await screen.findByRole('alert')
  expect(screen.queryByText('Нет промокодов')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  await screen.findByText('TESTONLY')
  expect(api.promo.adminToggle).toHaveBeenCalledTimes(1)
  expect(api.promo.adminCreate).not.toHaveBeenCalled()
})

it.each(['resolve', 'reject'] as const)('ignores an obsolete mount read that later %ss', async outcome => {
  const old = deferred<PromoEntry[]>()
  vi.mocked(api.promo.adminList).mockReturnValueOnce(old.promise)
  render(<StrictMode><Page /></StrictMode>)
  await screen.findByText('TESTONLY')
  await act(async () => {
    if (outcome === 'resolve') old.resolve([{ ...promo, code: 'OBSOLETE' }])
    else old.reject(new Error('old failure'))
  })
  expect(screen.getByText('TESTONLY')).toBeInTheDocument()
  expect(screen.queryByText('OBSOLETE')).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('does not launch a refresh when a toggle finishes after unmount', async () => {
  const mutation = deferred<PromoEntry>()
  vi.mocked(api.promo.adminToggle).mockReturnValueOnce(mutation.promise)
  const view = render(<Page />)
  await screen.findByText('TESTONLY')
  fireEvent.click(screen.getByTitle('Деактивировать'))
  view.unmount()
  await act(async () => mutation.resolve(promo))
  expect(api.promo.adminList).toHaveBeenCalledTimes(1)
})

it('preserves confirmed empty results and successful create with one mutation', async () => {
  vi.mocked(api.promo.adminList).mockResolvedValueOnce([])
  vi.mocked(api.promo.adminStats).mockResolvedValueOnce({ ...stats, total_promos: 0, active_promos: 0, total_activations: 0 })
  render(<Page />)
  await screen.findByText('Нет промокодов')
  expect(screen.getByRole('button', { name: 'Все (0)' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Создать промокод' }))
  fireEvent.change(screen.getByLabelText('Код *'), { target: { value: 'TESTONLY' } })
  fireEvent.change(screen.getByLabelText('Процент (%) *'), { target: { value: '12' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать', exact: true }))
  await screen.findByText('TESTONLY')
  expect(api.promo.adminCreate).toHaveBeenCalledTimes(1)
  expect(api.promo.adminList).toHaveBeenCalledTimes(2)
  expect(api.promo.adminToggle).not.toHaveBeenCalled()
})

it('recovers a failed post-create read without resending the creation', async () => {
  vi.mocked(api.promo.adminStats).mockResolvedValueOnce(stats).mockRejectedValueOnce(new Error('offline'))
  render(<Page />)
  await screen.findByText('TESTONLY')
  fireEvent.click(screen.getByRole('button', { name: 'Создать промокод' }))
  fireEvent.change(screen.getByLabelText('Код *'), { target: { value: 'NEWCODE' } })
  fireEvent.change(screen.getByLabelText('Процент (%) *'), { target: { value: '7' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать', exact: true }))
  await screen.findByRole('alert')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText('TESTONLY')).not.toBeInTheDocument()
  expect(screen.queryByText('Нет промокодов')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  await screen.findByText('TESTONLY')
  expect(api.promo.adminCreate).toHaveBeenCalledTimes(1)
  expect(api.promo.adminStats).toHaveBeenCalledTimes(3)
  expect(api.promo.adminToggle).not.toHaveBeenCalled()
})

it('contains a rejected toggle and never repeats it during read recovery', async () => {
  vi.mocked(api.promo.adminToggle).mockRejectedValueOnce(new Error('offline'))
  vi.mocked(api.promo.adminList).mockResolvedValueOnce([promo]).mockRejectedValueOnce(new Error('offline'))
  render(<Page />)
  await screen.findByText('TESTONLY')
  fireEvent.click(screen.getByTitle('Деактивировать'))
  await screen.findByRole('button', { name: 'Повторить загрузку' })
  expect(screen.queryByTitle('Деактивировать')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  await screen.findByText('TESTONLY')
  expect(api.promo.adminToggle).toHaveBeenCalledTimes(1)
})

it('blocks repeated toggle and create while one toggle is pending', async () => {
  const mutation = deferred<PromoEntry>()
  vi.mocked(api.promo.adminToggle).mockReturnValueOnce(mutation.promise)
  render(<Page />)
  await screen.findByText('TESTONLY')
  const toggle = screen.getByTitle('Деактивировать')
  fireEvent.click(toggle)
  fireEvent.click(toggle)
  expect(toggle).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Создать промокод' })).toBeDisabled()
  await act(async () => mutation.resolve(promo))
  await screen.findByText('TESTONLY')
  expect(api.promo.adminToggle).toHaveBeenCalledTimes(1)
})

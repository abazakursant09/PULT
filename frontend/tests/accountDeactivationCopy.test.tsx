import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AccountPage from '@/app/dashboard/account/page'
import { api } from '@/lib/api'
import { clearSession } from '@/lib/session'

const navigation = vi.hoisted(() => ({ router: { push: vi.fn() }, params: new URLSearchParams('tab=danger') }))
vi.mock('next/navigation', () => ({ useRouter: () => navigation.router, useSearchParams: () => navigation.params }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true, clearSession: vi.fn() }))
vi.mock('@/components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))
vi.mock('@/lib/api', () => ({ api: { account: { close: vi.fn() } } }))

const destination = { href: '/dashboard/account' }
afterEach(() => vi.unstubAllGlobals())
beforeEach(() => {
  vi.clearAllMocks()
  destination.href = '/dashboard/account'
  const originalWindow = window
  vi.stubGlobal('window', new Proxy(originalWindow, {
    get: (target, key) => key === 'location' ? destination : Reflect.get(target, key, target),
  }))
  vi.mocked(api.account.close).mockReset().mockResolvedValue({ ok: true, state: 'closed_recoverable', recovery_deadline: '2026-12-10T12:00:00Z', erasure_complete: false, message: 'synthetic deactivation' })
})

it('describes the 30-day recovery window without immediate erasure promises', async () => {
  render(<AccountPage />)
  expect(await screen.findByText('Деактивация аккаунта')).toBeInTheDocument()
  expect(screen.getByText(/Аккаунт будет закрыт/)).toHaveTextContent('В течение 30 дней')
  expect(screen.getByText(/Аккаунт будет закрыт/)).toHaveTextContent('доступ по текущим сессиям прекращается')
  expect(screen.getByText(/Аккаунт будет закрыт/)).toHaveTextContent('подтвердить новое письмо')
  expect(screen.queryByText(/После удаления|все ваши данные будут деактивированы|нельзя отменить|остановлены|прекратится обработка/i)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /удалить/i })).not.toBeInTheDocument()
  expect(api.account.close).not.toHaveBeenCalled()
})

it('uses deactivation labels in the confirmation and preserves cancellation without a request', async () => {
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать аккаунт' }))
  expect(screen.getByText(/Подтверждаю закрытие аккаунта/)).toHaveTextContent('в течение 30 дней')
  expect(screen.getByText(/Подтверждаю закрытие аккаунта/)).toHaveTextContent('не означает немедленного стирания')
  expect(screen.getByRole('button', { name: 'Да, деактивировать' })).toBeInTheDocument()
  expect(screen.queryByText(/нельзя отменить/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Отмена' }))
  expect(screen.getByRole('button', { name: 'Деактивировать аккаунт' })).toBeInTheDocument()
  expect(api.account.close).not.toHaveBeenCalled()
})

it('uses confirmed closure, pending state, session clearing and full login navigation', async () => {
  const result = { ok: true, state: 'closed_recoverable' as const, recovery_deadline: '2026-12-10T12:00:00Z', erasure_complete: false as const, message: 'synthetic closure' }
  let finish!: (value: typeof result) => void
  vi.mocked(api.account.close).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать аккаунт' }))
  const confirm = screen.getByRole('button', { name: 'Да, деактивировать' })
  fireEvent.click(confirm)
  expect(confirm).toBeDisabled()
  expect(api.account.close).toHaveBeenCalledExactlyOnceWith()
  expect(clearSession).not.toHaveBeenCalled()
  expect(navigation.router.push).not.toHaveBeenCalled()
  await act(async () => finish(result))
  await waitFor(() => expect(clearSession).toHaveBeenCalledTimes(1))
  expect(destination.href).toBe('/login')
})

it('preserves the server error and does not clear the session or redirect on rejection', async () => {
  vi.mocked(api.account.close).mockRejectedValueOnce(new Error('Сервис временно недоступен'))
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать аккаунт' }))
  fireEvent.click(screen.getByRole('button', { name: 'Да, деактивировать' }))
  expect(await screen.findByText('Сервис временно недоступен')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Деактивировать аккаунт' })).toBeEnabled()
  expect(api.account.close).toHaveBeenCalledTimes(1)
  expect(clearSession).not.toHaveBeenCalled()
  expect(navigation.router.push).not.toHaveBeenCalled()
})

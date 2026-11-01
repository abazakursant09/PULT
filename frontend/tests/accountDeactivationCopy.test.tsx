import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import AccountPage from '@/app/dashboard/account/page'
import { api } from '@/lib/api'
import { clearSession } from '@/lib/session'

const navigation = vi.hoisted(() => ({ router: { push: vi.fn() }, params: new URLSearchParams('tab=danger') }))
vi.mock('next/navigation', () => ({ useRouter: () => navigation.router, useSearchParams: () => navigation.params }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true, clearSession: vi.fn() }))
vi.mock('@/components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))
vi.mock('@/lib/api', () => ({ api: { account: { delete: vi.fn() } } }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.account.delete).mockReset().mockResolvedValue({ ok: true, message: 'synthetic deactivation' })
})

it('describes soft deactivation, retained data, denied sessions and recovery without erasure/stop promises', async () => {
  render(<AccountPage />)
  expect(await screen.findByText('Деактивация аккаунта')).toBeInTheDocument()
  expect(screen.getByText(/Аккаунт будет деактивирован, а не полностью удалён/)).toHaveTextContent('Сохранённые данные аккаунта остаются в системе')
  expect(screen.getByText(/Аккаунт будет деактивирован, а не полностью удалён/)).toHaveTextContent('доступ по текущим сессиям прекращается')
  expect(screen.getByText(/Аккаунт будет деактивирован, а не полностью удалён/)).toHaveTextContent('повторную регистрацию с тем же email')
  expect(screen.queryByText(/После удаления|все ваши данные будут деактивированы|нельзя отменить|остановлены|прекратится обработка/i)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /удалить/i })).not.toBeInTheDocument()
  expect(api.account.delete).not.toHaveBeenCalled()
})

it('uses deactivation labels in the confirmation and preserves cancellation without a request', async () => {
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать аккаунт' }))
  expect(screen.getByText(/текущие сессии потеряют доступ/)).toHaveTextContent('Данные не стираются полностью')
  expect(screen.getByText(/текущие сессии потеряют доступ/)).toHaveTextContent('повторная регистрация с тем же email')
  expect(screen.getByRole('button', { name: 'Да, деактивировать' })).toBeInTheDocument()
  expect(screen.queryByText(/нельзя отменить/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Отмена' }))
  expect(screen.getByRole('button', { name: 'Деактивировать аккаунт' })).toBeInTheDocument()
  expect(api.account.delete).not.toHaveBeenCalled()
})

it('keeps the existing compatibility method, pending state, session clearing and login navigation', async () => {
  let finish!: (value: { ok: boolean; message: string }) => void
  vi.mocked(api.account.delete).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать аккаунт' }))
  const confirm = screen.getByRole('button', { name: 'Да, деактивировать' })
  fireEvent.click(confirm)
  expect(confirm).toBeDisabled()
  expect(api.account.delete).toHaveBeenCalledExactlyOnceWith()
  expect(clearSession).not.toHaveBeenCalled()
  expect(navigation.router.push).not.toHaveBeenCalled()
  await act(async () => finish({ ok: true, message: 'synthetic deactivation' }))
  await waitFor(() => expect(clearSession).toHaveBeenCalledTimes(1))
  expect(navigation.router.push).toHaveBeenCalledWith('/login')
})

it('preserves the server error and does not clear the session or redirect on rejection', async () => {
  vi.mocked(api.account.delete).mockRejectedValueOnce(new Error('Сервис временно недоступен'))
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Деактивировать аккаунт' }))
  fireEvent.click(screen.getByRole('button', { name: 'Да, деактивировать' }))
  expect(await screen.findByText('Сервис временно недоступен')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Деактивировать аккаунт' })).toBeEnabled()
  expect(api.account.delete).toHaveBeenCalledTimes(1)
  expect(clearSession).not.toHaveBeenCalled()
  expect(navigation.router.push).not.toHaveBeenCalled()
})

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AccountPage from '@/app/dashboard/account/page'
import { api } from '@/lib/api'
import { clearSession } from '@/lib/session'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams('tab=profile') }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true, clearSession: vi.fn() }))
vi.mock('@/components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))
vi.mock('@/lib/api', () => ({ api: { auth: { logout: vi.fn() } } }))

const destination = { href: '/dashboard/account' }
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.auth.logout).mockReset().mockResolvedValue(undefined)
  destination.href = '/dashboard/account'
  const originalWindow = window
  vi.stubGlobal('window', new Proxy(originalWindow, {
    get: (target, key) => key === 'location' ? destination : Reflect.get(target, key, target),
  }))
})
afterEach(() => vi.unstubAllGlobals())

it('revokes the server session before clearing local state and fully navigating to login', async () => {
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Выйти из аккаунта' }))
  await waitFor(() => expect(destination.href).toBe('/login'))
  expect(api.auth.logout).toHaveBeenCalledTimes(1)
  expect(clearSession).toHaveBeenCalledTimes(1)
  expect(vi.mocked(api.auth.logout).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(clearSession).mock.invocationCallOrder[0])
})

it('does not claim logout or remove local state after a server/network failure, and permits retry', async () => {
  vi.mocked(api.auth.logout).mockRejectedValueOnce(new Error('synthetic offline'))
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Выйти из аккаунта' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось выйти')
  expect(clearSession).not.toHaveBeenCalled()
  expect(destination.href).toBe('/dashboard/account')
  fireEvent.click(screen.getByRole('button', { name: 'Выйти из аккаунта' }))
  await waitFor(() => expect(destination.href).toBe('/login'))
  expect(api.auth.logout).toHaveBeenCalledTimes(2)
})

it('disables repeat clicks while logout is pending and preserves the session until confirmation', async () => {
  let finish!: () => void
  vi.mocked(api.auth.logout).mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Выйти из аккаунта' }))
  const pending = screen.getByRole('button', { name: 'Выходим…' })
  expect(pending).toBeDisabled()
  fireEvent.click(pending)
  expect(api.auth.logout).toHaveBeenCalledTimes(1)
  expect(clearSession).not.toHaveBeenCalled()
  await act(async () => finish())
  expect(destination.href).toBe('/login')
})

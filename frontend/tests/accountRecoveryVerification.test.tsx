import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import VerifyEmailPage from '@/app/verify-email/page'
import { api } from '@/lib/api'
import { clearSession, setUser } from '@/lib/session'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
const router = { push }
vi.mock('next/navigation', () => ({ useRouter: () => router, useSearchParams: () => new URLSearchParams('token=synthetic') }))
vi.mock('@/lib/api', () => ({ api: { auth: { verifyEmail: vi.fn(), resendVerification: vi.fn() } } }))
vi.mock('@/lib/session', () => ({ clearSession: vi.fn(), setUser: vi.fn() }))

afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

it('requires normal login after recovery without creating a local authenticated profile', async () => {
  vi.useFakeTimers()
  vi.mocked(api.auth.verifyEmail).mockResolvedValue({ login_required: true, message: 'Аккаунт восстановлен. Войдите с паролем.' })
  await act(async () => { render(<VerifyEmailPage />) })
  expect(screen.getByText('Аккаунт восстановлен. Войдите с паролем.')).toBeInTheDocument()
  expect(clearSession).toHaveBeenCalledOnce()
  expect(setUser).not.toHaveBeenCalled()
  expect(push).not.toHaveBeenCalled()
  await act(async () => { vi.advanceTimersByTime(2000) })
  expect(push).toHaveBeenCalledWith('/login')
})

it('does not authenticate or redirect after a rejected recovery link', async () => {
  vi.mocked(api.auth.verifyEmail).mockRejectedValue(new Error('Ссылка недействительна'))
  await act(async () => { render(<VerifyEmailPage />) })
  expect(screen.getByText('Не удалось подтвердить')).toBeInTheDocument()
  expect(clearSession).not.toHaveBeenCalled()
  expect(setUser).not.toHaveBeenCalled()
  expect(push).not.toHaveBeenCalled()
})

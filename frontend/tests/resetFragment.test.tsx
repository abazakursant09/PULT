import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ResetPasswordPage from '@/app/reset-password/page'
import { api } from '@/lib/api'

// SECURITY-2C-3B — the raw reset token arrives in the URL FRAGMENT (#token=…), which the browser never
// sends to any server. The page's contract is narrow and load-bearing:
//   1. read the token ONLY from location.hash (never from the query string),
//   2. strip the fragment from the URL immediately (history.replaceState) so it can't leak,
//   3. keep the token ONLY in component memory — never localStorage / sessionStorage / cookie,
//   4. send it ONLY in the confirm request body,
//   5. never leave the token anywhere in the URL after mount.
const RAW = 'raw-reset-token-abc123XYZ'
const PW = 'NewPass0rd'

function setUrl(pathWithHashOrQuery: string) {
  window.history.replaceState(null, '', pathWithHashOrQuery)
}

describe('reset-password page: fragment-only token handling', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setUrl('/reset-password')
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('reads the token from the fragment and strips it from the URL immediately', async () => {
    setUrl('/reset-password#token=' + RAW)
    const replaceSpy = vi.spyOn(window.history, 'replaceState')
    render(<ResetPasswordPage />)
    await waitFor(() => expect(replaceSpy).toHaveBeenCalled())
    expect(window.location.hash).toBe('')                 // fragment removed
    expect(window.location.href).not.toContain(RAW)       // token nowhere in the URL after mount
  })

  it('ignores a token in the query string — no query fallback', async () => {
    setUrl('/reset-password?token=' + RAW)                 // query only, NO fragment
    const spy = vi.spyOn(api.auth, 'resetPassword').mockResolvedValue({ message: 'ok' } as never)
    render(<ResetPasswordPage />)
    await screen.findByText(/Недействительная ссылка/i)   // treated as no token
    expect(spy).not.toHaveBeenCalled()
  })

  it('submits the raw token ONLY in the request body and never persists it', async () => {
    setUrl('/reset-password#token=' + RAW)
    const spy = vi.spyOn(api.auth, 'resetPassword').mockResolvedValue({ message: 'ok' } as never)
    const lsSet = vi.spyOn(Storage.prototype, 'setItem')
    render(<ResetPasswordPage />)
    await waitFor(() => expect(window.location.hash).toBe(''))   // effect ran, token in memory

    await userEvent.type(screen.getByLabelText('Новый пароль'), PW)
    await userEvent.type(screen.getByLabelText('Подтвердите пароль'), PW)
    await userEvent.click(screen.getByRole('button', { name: /Сохранить/i }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith(RAW, PW))  // body-only delivery
    // never persisted to any web storage or cookie
    expect(lsSet.mock.calls.some(c => String(c[1]).includes(RAW))).toBe(false)
    expect(window.sessionStorage.getItem('token')).toBeNull()
    expect(document.cookie).not.toContain(RAW)
    // URL still carries no token
    expect(window.location.href).not.toContain(RAW)
  })

  it('shows the success screen and keeps the token out of the URL after submit', async () => {
    setUrl('/reset-password#token=' + RAW)
    vi.spyOn(api.auth, 'resetPassword').mockResolvedValue({ message: 'ok' } as never)
    render(<ResetPasswordPage />)
    await waitFor(() => expect(window.location.hash).toBe(''))

    await userEvent.type(screen.getByLabelText('Новый пароль'), PW)
    await userEvent.type(screen.getByLabelText('Подтвердите пароль'), PW)
    await userEvent.click(screen.getByRole('button', { name: /Сохранить/i }))

    await screen.findByText(/Пароль изменён/i)
    expect(window.location.href).not.toContain(RAW)       // token purged, never back in the URL
  })
})

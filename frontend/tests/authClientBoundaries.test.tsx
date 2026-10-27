import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoginPage from '@/app/login/page'
import { api, type AuthResponse } from '@/lib/api'
import * as session from '@/lib/session'
import { routerPush } from './setup'

const signedIn: AuthResponse = { user: {
  id: 'synthetic-user', email: 'seller@example.invalid', name: 'Seller',
  plan: 'master', is_verified: true, created_at: '2026-01-01T00:00:00Z',
} }
const networkGuard = globalThis.fetch

afterEach(() => {
  vi.restoreAllMocks()
  globalThis.fetch = networkGuard
  window.history.replaceState({}, '', '/')
})

describe('login return navigation stays on the application origin', () => {
  it.each([
    [null, '/dashboard'],
    ['/dashboard/stores?view=imports#latest', '/dashboard/stores?view=imports#latest'],
    ['/dashboard?next=https://elsewhere.invalid', '/dashboard?next=https://elsewhere.invalid'],
    ['//elsewhere.invalid/path', '/dashboard'],
    ['///elsewhere.invalid', '/dashboard'],
    ['/\\elsewhere.invalid', '/dashboard'],
    ['https://elsewhere.invalid', '/dashboard'],
    ['javascript:alert(1)', '/dashboard'],
    ['/\n/elsewhere.invalid', '/dashboard'],
    ['/part/..//elsewhere.invalid', '/dashboard'],
  ])('credentials: from=%j resolves to %s', async (from, expected) => {
    window.history.replaceState({}, '', '/login' + (from === null ? '' : '?from=' + encodeURIComponent(from)))
    vi.spyOn(api.auth, 'login').mockResolvedValue(signedIn)
    const { container } = render(<LoginPage />)
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'seller@example.invalid' } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'synthetic-password' } })
    fireEvent.submit(container.querySelector('form')!)
    await waitFor(() => expect(routerPush).toHaveBeenCalledExactlyOnceWith(expected))
  })

  it.each([
    ['//elsewhere.invalid', '/dashboard'],
    ['/dashboard/stores', '/dashboard/stores'],
  ])('MFA uses the same validated return path: %s', async (from, expected) => {
    window.history.replaceState({}, '', '/login?from=' + encodeURIComponent(from))
    vi.spyOn(api.auth, 'login').mockResolvedValue({ mfa_required: true, mfa_token: 'synthetic-challenge' })
    vi.spyOn(api.mfa, 'loginMfa').mockResolvedValue(signedIn)
    const { container } = render(<LoginPage />)
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'seller@example.invalid' } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'synthetic-password' } })
    fireEvent.submit(container.querySelector('form')!)
    const code = await screen.findByPlaceholderText('000000')
    expect(routerPush).not.toHaveBeenCalled()
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.submit(container.querySelector('form')!)
    await waitFor(() => expect(routerPush).toHaveBeenCalledExactlyOnceWith(expected))
  })
})

describe('permission denial is not an expired session', () => {
  it.each([401, 403, 429])('JSON and multipart preserve HTTP %i semantics without retries', async status => {
    const clear = vi.spyOn(session, 'clearSession').mockImplementation(() => {})
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ detail: 'synthetic denial' }), {
      status, headers: { 'Content-Type': 'application/json' },
    }))
    globalThis.fetch = fetchMock
    const location = { href: '/dashboard' }
    // Only transport reads this object; restore the actual DOM window before cleanup.
    vi.stubGlobal('window', { location })
    try {
      await expect(api.today.getSummary()).rejects.toMatchObject({ status, message: 'synthetic denial' })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(clear).toHaveBeenCalledTimes(status === 401 ? 1 : 0)
      expect(location.href).toBe(status === 401 ? '/login?reason=session_expired' : '/dashboard')
      clear.mockClear()
      location.href = '/dashboard'
      await expect(api.csvImport.upload(new File(['synthetic'], 'test.csv'), 'synthetic-store')).rejects.toMatchObject({ status })
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(clear).toHaveBeenCalledTimes(status === 401 ? 1 : 0)
      expect(location.href).toBe(status === 401 ? '/login?reason=session_expired' : '/dashboard')
    } finally { vi.unstubAllGlobals() }
  })

  it('auth endpoint 401 remains a form error, not a forced redirect', async () => {
    const clear = vi.spyOn(session, 'clearSession')
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'synthetic credentials error' }), { status: 401 }))
    await expect(api.auth.login('seller@example.invalid', 'invalid')).rejects.toMatchObject({ status: 401 })
    expect(clear).not.toHaveBeenCalled()
  })
})

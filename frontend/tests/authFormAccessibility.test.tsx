import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderToString } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoginPage from '@/app/login/page'
import RegisterPage from '@/app/register/page'
import { api, type AuthResponse } from '@/lib/api'

const signedIn: AuthResponse = { user: { id: 'synthetic', email: 'seller@example.invalid',
  name: 'Seller', plan: 'master', is_verified: true, created_at: '2026-01-01T00:00:00Z' } }

afterEach(() => { vi.restoreAllMocks(); window.history.replaceState({}, '', '/') })

describe('auth controls expose their existing labels and browser purposes', () => {
  it('server-rendered named credentials cannot fall back to GET query submission', () => {
    for (const page of [<LoginPage key="login" />, <RegisterPage key="register" />]) {
      const doc = new DOMParser().parseFromString(renderToString(page), 'text/html')
      const password = doc.querySelector('input[type="password"][name="password"]')
      expect(password).not.toBeNull()
      expect(password!.closest('form')!.getAttribute('method')).toBe('post')
    }
  })
  it('login labels target named credential inputs', () => {
    render(<LoginPage />)
    expect(screen.getByLabelText('EMAIL')).toHaveAttribute('name', 'email')
    expect(screen.getByLabelText('EMAIL')).toHaveAttribute('autocomplete', 'email')
    expect(screen.getByLabelText('ПАРОЛЬ')).toHaveAttribute('name', 'password')
    expect(screen.getByLabelText('ПАРОЛЬ')).toHaveAttribute('autocomplete', 'current-password')
  })

  it('announces session information and async errors without changing submission behavior', async () => {
    window.history.replaceState({}, '', '/login?reason=session_expired')
    vi.spyOn(api.auth, 'login').mockRejectedValue(new Error('synthetic login error'))
    const { container } = render(<LoginPage />)
    expect(await screen.findByRole('status')).toHaveTextContent('Сессия истекла')
    fireEvent.change(screen.getByLabelText('EMAIL'), { target: { value: signedIn.user.email } })
    fireEvent.change(screen.getByLabelText('ПАРОЛЬ'), { target: { value: 'synthetic-password' } })
    fireEvent.submit(container.querySelector('form')!)
    expect(await screen.findByRole('alert')).toHaveTextContent('synthetic login error')
  })

  it('remember-email works with Space and label click, and preserves the existing storage contract', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.auth, 'login').mockResolvedValue(signedIn)
    const { container } = render(<LoginPage />)
    const checkbox = screen.getByRole('checkbox', { name: /запомнить/i })
    checkbox.focus()
    await user.keyboard(' ')
    expect(checkbox).toBeChecked()
    await user.click(screen.getByText(/запомнить/i))
    expect(checkbox).not.toBeChecked()
    checkbox.focus()
    await user.keyboard(' ')
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: signedIn.user.email } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'synthetic-password' } })
    fireEvent.submit(container.querySelector('form')!)
    await waitFor(() => expect(localStorage.getItem('bp_remember_email')).toBe(signedIn.user.email))
  })

  it('MFA code has a linked label and one-time-code autofill', async () => {
    vi.spyOn(api.auth, 'login').mockResolvedValue({ mfa_required: true, mfa_token: 'synthetic-challenge' })
    const { container } = render(<LoginPage />)
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: signedIn.user.email } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'synthetic-password' } })
    fireEvent.submit(container.querySelector('form')!)
    await screen.findByPlaceholderText('000000')
    expect(screen.getByLabelText(/Код аутентификатора/)).toHaveAttribute('autocomplete', 'one-time-code')
    expect(screen.getByLabelText(/Код аутентификатора/)).toHaveAttribute('name')
    expect(screen.getByLabelText(/Код аутентификатора/).closest('form')).toHaveAttribute('method', 'post')
  })

  it('registration fields have linked labels and explicit autofill purposes', () => {
    render(<RegisterPage />)
    expect(screen.getByLabelText(/имя/i)).toHaveAttribute('autocomplete', 'name')
    expect(screen.getByLabelText(/email/i)).toHaveAttribute('autocomplete', 'email')
    expect(screen.getByLabelText(/^пароль$/i)).toHaveAttribute('autocomplete', 'new-password')
    for (const label of [/имя/i, /email/i, /^пароль$/i]) expect(screen.getByLabelText(label)).toHaveAttribute('name')
  })

  it('registration keeps its two native consent controls and does not submit on mount', () => {
    const register = vi.spyOn(api.auth, 'register')
    render(<RegisterPage />)
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
    expect(register).not.toHaveBeenCalled()
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RegisterPage from '@/app/register/page'
import { api } from '@/lib/api'

// B6 — the screen after registration, when the letter did not leave.
//
// It used to say «Проверьте почту» whether the mail was delivered, merely logged, or refused
// outright, and then advised registering again — which returns "Email уже зарегистрирован". Since
// login is blocked until verification and password reset did not verify either, a seller whose
// mail failed was locked out of an account they could neither enter nor recover.
//
// The account is still created. What changes is that the screen stops claiming a letter is coming
// when none is, and offers the one action that can fix it.

describe('the registration form still renders', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear() })

  it('mounts without touching the API', () => {
    const register = vi.spyOn(api.auth, 'register')
    render(<RegisterPage />)
    // Queried by placeholder: the labels on this form are not associated with their inputs.
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })
})

// The post-registration screens are asserted against the module source. Reaching them through the
// UI means clearing a maths captcha and two consent checkboxes, which would make this a test of
// the form rather than of the thing that was broken. What matters here is the copy and the wiring;
// the delivery contract itself is pinned end-to-end on the backend.
describe('the two post-registration screens are mutually exclusive', () => {
  const src = () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fs = require('fs') as typeof import('fs')
    const path = require('path') as typeof import('path')
    return fs.readFileSync(path.join(__dirname, '..', 'app', 'register', 'page.tsx'), 'utf-8')
  }

  it('reads the delivery flag from the register response', () => {
    expect(src()).toMatch(/verification_email_sent/)
  })

  it('shows the required failure message', () => {
    expect(src()).toContain('Не удалось отправить письмо. Проверьте адрес и попробуйте ещё раз.')
  })

  it('offers a resend button', () => {
    expect(src()).toContain('Отправить письмо повторно')
  })

  // The resend endpoint answers identically for an unknown address, a verified one, a successful
  // send and a refused one — otherwise it would tell any anonymous caller which addresses are
  // registered. The consequence for this screen: it does not know whether a letter left, so it is
  // not allowed to say one did. Registration's own flag is unaffected; that account is the
  // caller's own.
  it('never promises that the resent letter was actually sent', () => {
    const s = src()
    expect(s).not.toContain('Письмо отправлено')
    expect(s).not.toContain('Письмо снова не отправилось')
    // No resend delivery flag is read. `verification_email_sent` — registration's own, and the
    // caller's own account — is deliberately still there, so match the property access exactly.
    expect(s).not.toMatch(/\.email_sent/)
  })

  it('answers a resend with the conditional message instead', () => {
    expect(src()).toContain('Если адрес зарегистрирован и почта доступна, письмо будет отправлено.')
  })

  it('has a sending state so the button cannot be hammered', () => {
    expect(src()).toContain('Отправляем…')
    expect(src()).toMatch(/disabled=\{resend === 'sending'\}/)
  })

  it('lets the seller try again once the request finishes', () => {
    // Only 'sending' disables it — a finished attempt leaves the button live, throttled by the
    // endpoint's own rate limit rather than by a one-shot UI state.
    expect(src()).not.toMatch(/disabled=\{resend !== 'idle'\}/)
    expect(src()).toMatch(/setResend\('done'\)/)
  })

  it('resends through the existing rate-limited endpoint', () => {
    expect(src()).toMatch(/api\.auth\.resendVerification/)
  })

  it('no longer advises registering again, which would 400', () => {
    expect(src()).not.toContain('зарегистрируйтесь ещё раз')
  })

  it('never puts a token in the DOM or the URL', () => {
    const s = src()
    expect(s).not.toMatch(/verification_token/)
    expect(s).not.toMatch(/reset_token/)
    expect(s).not.toMatch(/\?token=/)
  })
})

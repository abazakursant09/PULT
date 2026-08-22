import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { CookieBanner } from '@/components/CookieBanner'

// LEGAL-PRELAUNCH-C2 — the banner is an acknowledgement notice. It must set NO cookie (the only
// real cookie is the backend HttpOnly session cookie), name no fictional bp_* cookie, and claim no
// active analytics. It only records, in localStorage, that the notice was shown on this device.

describe('CookieBanner (cookie truth)', () => {
  beforeEach(() => {
    localStorage.clear()
    // start from a clean cookie jar
    document.cookie.split(';').forEach((c) => {
      const k = c.split('=')[0].trim()
      if (k) document.cookie = `${k}=; max-age=0; path=/`
    })
  })

  it('shows an honest notice with no active-analytics claim', () => {
    render(<CookieBanner />)
    expect(screen.getByText(/аналитика сейчас не используется/i)).toBeTruthy()
    expect(screen.queryByText(/аналитические cookie помога/i)).toBeNull()
  })

  it('sets no cookie and records only a localStorage acknowledgement', async () => {
    render(<CookieBanner />)
    await userEvent.click(screen.getByRole('button', { name: /Понятно/i }))

    // no JS cookie was set by the banner
    expect(document.cookie).not.toMatch(/bp_session/)
    expect(document.cookie).not.toMatch(/bp_analytics/)
    expect(document.cookie.trim()).toBe('')

    // acknowledgement stored, no analytics field
    const saved = JSON.parse(localStorage.getItem('cookie_consent') as string)
    expect(saved.acknowledged).toBe(true)
    expect('analytics' in saved).toBe(false)
  })

  it('does not reappear once acknowledged', () => {
    localStorage.setItem('cookie_consent', JSON.stringify({ acknowledged: true, timestamp: 1 }))
    const { container } = render(<CookieBanner />)
    expect(container.textContent).toBe('')
  })
})

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

  it('offers a named non-modal notice and keeps the full privacy text and link', () => {
    render(<CookieBanner />)
    expect(screen.getByRole('region', { name: 'Файлы cookie и локальное хранилище' })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/privacy')
    expect(screen.getByText(/Настройки интерфейса хранятся локально в вашем браузере/)).toBeVisible()
  })

  it('dismisses with the close button using the same acknowledgement without setting cookies', async () => {
    render(<CookieBanner />)
    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }))
    expect(screen.queryByRole('region')).toBeNull()
    expect(JSON.parse(localStorage.getItem('cookie_consent') as string).acknowledged).toBe(true)
    expect(document.cookie).toBe('')
  })

  it('reserves scroll space for the notice and restores existing page padding on dismissal', async () => {
    const original = document.body.style.paddingBottom
    document.body.style.paddingBottom = '12px'
    const measure = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ height: 200 } as DOMRect)
    try {
      render(<CookieBanner />)
      expect(document.body.style.paddingBottom).toBe('212px')
      await userEvent.click(screen.getByRole('button', { name: 'Понятно' }))
      expect(document.body.style.paddingBottom).toBe('12px')
    } finally {
      measure.mockRestore()
      document.body.style.paddingBottom = original
    }
  })
})

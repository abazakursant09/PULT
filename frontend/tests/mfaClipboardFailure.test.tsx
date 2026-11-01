import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AccountPage from '@/app/dashboard/account/page'
import SecurityPage from '@/app/dashboard/security/page'
import { api } from '@/lib/api'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams('tab=security') }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true, clearSession: vi.fn() }))
vi.mock('@/components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const message = 'Не удалось скопировать ключ. Выделите и скопируйте его вручную.'

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.mfa, 'status').mockResolvedValue({ enabled: false })
  vi.spyOn(api.mfa, 'setup').mockResolvedValue({ secret: 'TESTONLY', otpauth: 'otpauth://totp/Test?secret=TESTONLY' })
  vi.spyOn(api.mfa, 'verify').mockRejectedValue(new Error('Unexpected verify'))
  vi.spyOn(api.mfa, 'disable').mockRejectedValue(new Error('Unexpected disable'))
})

afterEach(() => {
  cleanup()
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
  else Reflect.deleteProperty(navigator, 'clipboard')
})

for (const surface of ['account', 'security'] as const) {
  for (const failure of ['denied', 'unavailable'] as const) {
    it(`${surface} handles ${failure} clipboard, allows retry, clears stale copied state`, async () => {
      const writeText = vi.fn().mockRejectedValue(new DOMException('Denied', 'NotAllowedError'))
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: failure === 'denied' ? { writeText } : undefined })
      render(surface === 'account' ? <AccountPage /> : <SecurityPage />)
      fireEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }))
      await screen.findByText('TESTONLY')
      const copyButton = () => surface === 'account'
        ? screen.getByRole('button', { name: 'Скопировать ключ 2FA' })
        : screen.getByTitle('Скопировать')
      fireEvent.click(copyButton())
      expect(await screen.findByRole('alert')).toHaveTextContent(message)
      expect(copyButton().querySelector('.lucide-check')).toBeNull()
      expect(screen.getByText('TESTONLY')).toBeInTheDocument()
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
      writeText.mockResolvedValueOnce(undefined)
      fireEvent.click(copyButton())
      await waitFor(() => expect(copyButton().querySelector('.lucide-check')).not.toBeNull())
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      fireEvent.click(copyButton())
      expect(await screen.findByRole('alert')).toHaveTextContent(message)
      expect(copyButton().querySelector('.lucide-check')).toBeNull()
      expect(api.mfa.setup).toHaveBeenCalledTimes(1)
      expect(api.mfa.verify).not.toHaveBeenCalled()
      expect(api.mfa.disable).not.toHaveBeenCalled()
    })
  }
}

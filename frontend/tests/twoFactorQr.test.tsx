import { readdirSync, readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AccountPage from '@/app/dashboard/account/page'
import { api } from '@/lib/api'

// Enabling 2FA used to render the QR with <img src="https://api.qrserver.com/...?data=<otpauth>">.
// The otpauth URI carries the raw TOTP shared secret, so every seller who turned on 2FA handed
// their second factor to a host we do not control, in a URL, which that host logs. Anyone with
// that log could mint valid codes. The QR is now drawn in the browser and the secret never
// leaves it. These tests fail the day it leaves again.

const ROOT = join(__dirname, '..')

/** Every .tsx under a directory. Walked by hand — this guard must not need a new dependency. */
function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e =>
    e.isDirectory() ? tsxFiles(join(dir, e.name))
      : e.name.endsWith('.tsx') ? [join(dir, e.name)] : [])
}
const ACCOUNT = join(ROOT, 'app', 'dashboard', 'account', 'page.tsx')

const OTPAUTH = 'otpauth://totp/PULT:seller@example.com?secret=JBSWY3DPEHPK3PXP&issuer=PULT'
const SECRET = 'JBSWY3DPEHPK3PXP'

/** Every host that has ever drawn a QR for someone else. */
const EXTERNAL_QR_HOSTS = [
  'api.qrserver.com',
  'chart.googleapis.com',
  'quickchart.io',
  'goqr.me',
  'qrcode.tec-it.com',
]

function mockAccountApis() {
  vi.spyOn(api.mfa, 'status').mockResolvedValue({ enabled: false } as never)
  vi.spyOn(api.mfa, 'setup').mockResolvedValue({ secret: SECRET, otpauth: OTPAUTH } as never)
  vi.spyOn(api.telegram, 'getChatId').mockResolvedValue({ telegram_chat_id: null } as never)
  vi.spyOn(api.telegram, 'getSettings').mockRejectedValue(new Error('not needed'))
}

/** Open Аккаунт → Безопасность → Включить 2FA, and hand back the setup panel. */
async function openTwoFactorSetup() {
  const user = userEvent.setup()
  render(<AccountPage />)

  await user.click(await screen.findByRole('button', { name: /Безопасность/ }))
  await user.click(await screen.findByRole('button', { name: /Включить 2FA/ }))
  await waitFor(() => expect(api.mfa.setup).toHaveBeenCalled())

  return user
}

describe('the 2FA secret never leaves the browser', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    localStorage.setItem('token', 'test-token')
    localStorage.setItem('user', JSON.stringify({ name: 'Seller', email: 's@example.com' }))
    mockAccountApis()
  })

  it('makes no request to any external QR service when 2FA is enabled', async () => {
    // The real proof: watch the network. Nothing may reach out, to anyone.
    const fetchSpy = vi.spyOn(globalThis, 'fetch')

    await openTwoFactorSetup()

    await screen.findByRole('img', { name: /QR-код/i })

    for (const call of fetchSpy.mock.calls) {
      const url = String(call[0])
      expect(url).not.toMatch(/qrserver|googleapis|quickchart|goqr|tec-it/i)
      expect(url).not.toMatch(/otpauth/i)
      expect(url).not.toMatch(SECRET)
    }
  })

  it('puts the otpauth URI in no src, href, or any other network-bound attribute', async () => {
    await openTwoFactorSetup()
    await screen.findByRole('img', { name: /QR-код/i })

    for (const el of Array.from(document.querySelectorAll('*'))) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.value, `<${el.tagName.toLowerCase()} ${attr.name}> must not carry the secret`)
          .not.toMatch(/otpauth|JBSWY3DPEHPK3PXP/i)
      }
    }
  })

  it('renders the QR locally, as an inline svg — not as a remote image', async () => {
    await openTwoFactorSetup()

    const qr = await screen.findByRole('img', { name: /QR-код/i })

    expect(qr.tagName.toLowerCase()).toBe('svg')
    expect(qr.querySelectorAll('path').length).toBeGreaterThan(0)
    expect(document.querySelector('img')).toBeNull()
  })

  it('still shows the manual key, so a failed QR never blocks setup', async () => {
    await openTwoFactorSetup()

    expect(await screen.findByText(SECRET)).toBeInTheDocument()
    expect(screen.getByText(/введите ключ вручную/i)).toBeInTheDocument()
    // The QR sits inside its own ErrorBoundary precisely so that this path survives it.
    expect(readFileSync(ACCOUNT, 'utf-8')).toMatch(/<ErrorBoundary[\s\S]{0,400}<QRCodeSVG/)
  })
})

describe('no external QR host survives in the source', () => {
  it('names none of them, anywhere on the account page', () => {
    const src = readFileSync(ACCOUNT, 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '')  // drop the comment that explains why

    for (const host of EXTERNAL_QR_HOSTS) expect(src).not.toMatch(host)
    expect(src).not.toMatch(/https?:\/\/[^"'`\s]*qr/i)
  })

  it('places the otpauth URI in no src or href, on any page in the app', () => {
    // A repo-wide guard, not just this page: the security page used to hand otpauth to an
    // <a href> as an "open in your authenticator" deep link. That reached no network — the OS
    // handles the scheme — but it put the raw secret in a DOM attribute, and the rule we can
    // actually enforce is that it is never in one.
    const files = tsxFiles(join(ROOT, 'app'))
    expect(files.length).toBeGreaterThan(10)

    for (const f of files) {
      const src = readFileSync(f, 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '')
      expect(src, `${f} must not put otpauth in a network-bound attribute`)
        .not.toMatch(/(src|href)\s*=\s*\{?[^}\n]*otpauth/i)
    }
  })

  it('keeps the MFA API contract exactly as it was', () => {
    const src = readFileSync(ACCOUNT, 'utf-8')

    expect(src).toMatch(/api\.mfa\.setup\(\)/)
    expect(src).toMatch(/api\.mfa\.verify\(/)
    expect(src).toMatch(/api\.mfa\.disable\(/)
  })
})

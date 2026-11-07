import { createHash, createHmac } from 'crypto'
import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { expect, test, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { API, WEB, PASSWORD, go, login, query, type Seller } from './pg-helpers'

const REQUEST_TIMEOUT = 15_000
const STEP_TIMEOUT = 180_000
const SESSION_NAMES = new Set(['pult_session_dev', '__Host-pult_session'])

async function loopbackOnly(context: BrowserContext) {
  await context.route('**/*', route => {
    try {
      return [API, WEB].includes(new URL(route.request().url()).origin)
        ? route.continue() : route.abort()
    } catch { return route.abort() }
  })
}

function responseFor(page: Page, method: string, route: string) {
  return page.waitForResponse(response => response.url() === API + route
    && response.request().method() === method, { timeout: 30_000 })
}

async function probe(seller: Seller, route: string, data?: Record<string, unknown>) {
  const url = new URL(route, API)
  if (url.origin !== API || !route.startsWith('/api/')) throw new Error('Only owned API probes are allowed')
  // APIRequestContext shares this browser's cookies. Never follow a redirect off the owned API.
  return seller.context.request.fetch(url.href, {
    method: data === undefined ? 'GET' : 'POST', data,
    headers: { Origin: WEB }, timeout: REQUEST_TIMEOUT, maxRedirects: 0,
  })
}

async function session(seller: Seller, status = 200) {
  const response = await probe(seller, '/api/auth/me')
  expect(response.status()).toBe(status)
  if (status === 200) {
    const user = await response.json()
    expect(user.id).toBe(seller.id)
    expect(user.email).toBe(seller.email)
  }
}

async function cookie(context: BrowserContext) {
  const cookies = (await context.cookies(API)).filter(item => SESSION_NAMES.has(item.name))
  expect(cookies.length).toBeLessThanOrEqual(1)
  if (cookies[0]) {
    expect(cookies[0].httpOnly).toBe(true)
    expect(cookies[0].sameSite).toBe('Lax')
  }
  return cookies[0]?.value
}

function version(seller: Seller): number {
  const rows = query('SELECT token_version FROM users WHERE id=%s', [seller.id])
  expect(rows).toHaveLength(1)
  return Number(rows[0].token_version)
}

function factorState(seller: Seller): { enabled: boolean; last_totp_step: string | number | null } {
  const rows = query('SELECT enabled,last_totp_step FROM mfa_secrets WHERE user_id=%s', [seller.id])
  expect(rows).toHaveLength(1)
  return rows[0]
}

// RFC 6238 / the app's SHA1, six-digit, 30-second factor. No dependency or auth mock.
function totp(secret: string, step: number): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let value = 0, bits = 0
  const bytes: number[] = []
  for (const char of secret.toUpperCase().replace(/=+$/, '')) {
    const digit = alphabet.indexOf(char)
    if (digit < 0) throw new Error('Invalid owned setup secret encoding')
    value = (value << 5) | digit
    bits += 5
    if (bits >= 8) {
      bits -= 8
      bytes.push((value >>> bits) & 255)
      value &= (1 << bits) - 1
    }
  }
  const counter = Buffer.alloc(8)
  counter.writeUInt32BE(Math.floor(step / 0x100000000), 0)
  counter.writeUInt32BE(step >>> 0, 4)
  const digest = createHmac('sha1', Buffer.from(bytes)).update(counter).digest()
  const offset = digest[digest.length - 1] & 15
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, '0')
}

async function freshFactor(seller: Seller, secret: string) {
  let factor = { code: '', step: -1 }
  await expect.poll(() => {
    const state = factorState(seller)
    const last = state.last_totp_step === null ? -1 : Number(state.last_totp_step)
    const now = Date.now()
    const step = Math.floor(now / 30_000)
    if (step <= last || 30_000 - now % 30_000 < 4_000) return false
    const code = totp(secret, step)
    // Do not accidentally claim the next step if adjacent steps have a six-digit collision.
    if (code === totp(secret, step + 1)) return false
    factor = { code, step }
    return true
  }, { message: 'An unspent current TOTP step is available', timeout: 35_000,
    intervals: [250, 500, 1_000] }).toBe(true)
  return factor
}

function wrongFactor(secret: string): string {
  const step = Math.floor(Date.now() / 30_000)
  const valid = new Set([-2, -1, 0, 1, 2].map(offset => totp(secret, step + offset)))
  for (let candidate = 0; candidate < 1_000_000; candidate++) {
    const code = String(candidate).padStart(6, '0')
    if (!valid.has(code)) return code
  }
  throw new Error('No invalid test factor available')
}

async function credentials(seller: Seller, password: string) {
  const response = responseFor(seller.page, 'POST', '/api/auth/login')
  await login(seller, password)
  return response
}

async function passwordLogin(seller: Seller, password: string) {
  const response = await credentials(seller, password)
  expect(response.status()).toBe(200)
  const result = await response.json()
  expect(Boolean(result.mfa_required)).toBe(false)
  expect('access_token' in result).toBe(false)
  await seller.page.waitForURL(WEB + '/dashboard', { timeout: 60_000 })
  await session(seller)
}

async function isolation(a: Seller, b: Seller) {
  expect(Boolean(a.storeId || b.storeId), 'Journey must supply at least one owned store').toBe(true)
  for (const [owner, other] of [[a, b], [b, a]]) {
    const response = await probe(owner, '/api/marketplace-accounts?include_stores=true')
    expect(response.status()).toBe(200)
    const accounts: { id: string; stores: { id: string }[] | null }[] = await response.json()
    const owned = query('SELECT m.id FROM marketplace_accounts m JOIN workspaces w '
      + 'ON w.id=m.workspace_id WHERE w.owner_user_id=%s', [owner.id])
    expect(accounts.map(account => account.id).sort()).toEqual(owned.map(row => row.id).sort())
    const storeIds = accounts.flatMap(account => (account.stores ?? []).map(store => store.id))
    if (owner.storeId) {
      expect(storeIds).toContain(owner.storeId)
      expect((await probe(owner, `/api/marketplace-stores/${owner.storeId}/products`)).status()).toBe(200)
    }
    if (other.storeId) {
      expect(storeIds).not.toContain(other.storeId)
      expect((await probe(owner, `/api/marketplace-stores/${other.storeId}/products`)).status()).toBe(404)
    }
    if (other.importId) {
      expect((await probe(owner, `/api/import/${other.importId}/conflicts`)).status()).toBe(404)
    }
  }
}

/** After journey/restart/restore: source backend active, both original sellers signed in.
 * Leaves A signed in with PASSWORD + '-Reset', MFA disabled; leaves B's session untouched.
 * Reset capture is explicitly sent=false, not SMTP proof. No password-change surface is invented.
 */
export async function runSessionCases(browser: Browser, a: Seller, b: Seller) {
  const peers: BrowserContext[] = []
  const newPassword = PASSWORD + '-Reset'
  let secret = '', pendingChallenge = ''
  let factorPeer: Seller | undefined
  const root = process.env.PULT_E2E_PG_ROOT
  if (!root || !path.isAbsolute(root)) throw new Error('Absolute owned PG root is required')
  const capturePath = path.join(root, `reset-${createHash('sha256').update(a.email).digest('hex')}.json`)
  const bVersion = version(b)
  const bCookie = await cookie(b.context)
  expect(Boolean(bCookie)).toBe(true)

  async function unchangedB() {
    await session(b)
    expect(version(b)).toBe(bVersion)
    // Boolean comparisons keep cookies/tokens out of assertion output.
    expect((await cookie(b.context)) === bCookie).toBe(true)
  }
  async function peer(): Promise<Seller> {
    const context = await browser.newContext({ storageState: await a.context.storageState(), serviceWorkers: 'block' })
    peers.push(context)
    await loopbackOnly(context)
    context.setDefaultTimeout(20_000)
    return { ...a, context, page: await context.newPage() }
  }

  try {
    await loopbackOnly(a.context)
    await loopbackOnly(b.context)

    await test.step('Session: UI logout revokes A copies, not B', async () => {
      await session(a)
      await unchangedB()
      await isolation(a, b)
      const copied = await peer()
      await session(copied)
      const before = version(a)
      await go(a.page, '/')
      await a.page.getByRole('button', { name: /Меню пользователя/ }).click()
      const loggedOut = responseFor(a.page, 'POST', '/api/auth/logout')
      await a.page.getByRole('menuitem', { name: 'Выйти', exact: true }).click()
      expect((await loggedOut).status()).toBe(204)
      await a.page.waitForURL(WEB + '/login', { timeout: 30_000 })
      expect(version(a)).toBe(before + 1)
      expect((await cookie(a.context)) === undefined).toBe(true)
      await session(a, 401)
      await session(copied, 401)
      // A stale cookie can pass the edge presence guard, never the data API.
      await go(copied.page, '/dashboard/stores')
      await copied.page.waitForURL(WEB + '/login?reason=session_expired', { timeout: 30_000 })
      expect(await copied.page.evaluate(() => localStorage.getItem('user') === null)).toBe(true)
      expect((await probe(a, '/api/auth/logout', {})).status()).toBe(204)
      expect(version(a)).toBe(before + 1)
      await passwordLogin(a, PASSWORD)
      await unchangedB()
    }, { timeout: STEP_TIMEOUT })

    await test.step('Session: captured UI reset is single-use and revokes old sessions', async () => {
      const copied = await peer()
      await session(copied)
      const before = version(a)
      expect(existsSync(capturePath), 'Only one owned reset request is permitted').toBe(false)
      await go(a.page, '/forgot-password')
      await a.page.locator('#email').fill(a.email)
      const requested = responseFor(a.page, 'POST', '/api/auth/forgot-password')
      await a.page.getByRole('button', { name: 'Получить ссылку', exact: true }).click()
      expect((await requested).status()).toBe(200)
      await expect(a.page.getByText('Если этот email зарегистрирован, мы отправили на него ссылку для сброса пароля.',
        { exact: true })).toBeVisible()
      await expect.poll(() => existsSync(capturePath), { timeout: 10_000 }).toBe(true)
      const capture = JSON.parse(readFileSync(capturePath, 'utf8'))
      expect(capture.email).toBe(a.email)
      expect(capture.sent).toBe(false)
      expect(typeof capture.token === 'string' && capture.token.length >= 32).toBe(true)
      const token: string = capture.token
      const [stored] = query('SELECT reset_token FROM users WHERE id=%s', [a.id])
      expect(stored.reset_token === createHash('sha256').update(token).digest('hex')).toBe(true)
      expect(version(a)).toBe(before)
      await session(a)
      await session(copied)
      const stale = await probe(a, '/api/auth/reset-password', { token: 'not-a-live-reset-token', password: newPassword })
      expect(stale.status()).toBe(400)
      await go(a.page, '/reset-password#token=' + encodeURIComponent(token))
      // Never include the raw fragment in an assertion message, step name, or console output.
      await expect.poll(() => new URL(a.page.url()).hash === '', { timeout: 10_000 }).toBe(true)
      expect(new URL(a.page.url()).pathname).toBe('/reset-password')
      await a.page.locator('#password').fill(newPassword)
      await a.page.locator('#confirm').fill(newPassword)
      const confirmed = responseFor(a.page, 'POST', '/api/auth/reset-password')
      await a.page.getByRole('button', { name: 'Сохранить пароль', exact: true }).click()
      const reset = await confirmed
      expect(reset.status()).toBe(200)
      expect(reset.headers()['set-cookie'] === undefined).toBe(true)
      await expect(a.page.getByRole('heading', { name: 'Пароль изменён!', exact: true })).toBeVisible()
      await a.page.waitForURL(WEB + '/login', { timeout: 30_000 })
      expect(version(a)).toBe(before + 1)
      await session(a, 401) // Reset never signs the browser in automatically.
      await session(copied, 401)
      const reused = await probe(a, '/api/auth/reset-password', { token, password: newPassword + '-MustNotApply' })
      expect(reused.status()).toBe(400)
      expect((await reused.json()).detail).toBe((await stale.json()).detail)
      expect(version(a)).toBe(before + 1)
      expect((await credentials(a, PASSWORD)).status()).toBe(401)
      await expect(a.page.getByRole('alert').filter({ hasText: 'Неверный email или пароль' }))
        .toContainText('Неверный email или пароль')
      await passwordLogin(a, newPassword)
      await unchangedB()
    }, { timeout: STEP_TIMEOUT })

    await test.step('Session: real UI MFA enable rotates A and revokes its peer', async () => {
      const copied = await peer()
      const before = version(a), oldCookie = await cookie(a.context)
      await go(a.page, '/dashboard/account?tab=security')
      const setupResponse = responseFor(a.page, 'POST', '/api/auth/mfa/setup')
      await a.page.getByRole('button', { name: 'Включить 2FA', exact: true }).click()
      const setup = await setupResponse
      expect(setup.status()).toBe(200)
      const body = await setup.json()
      expect(typeof body.secret === 'string' && /^[A-Z2-7]+=*$/.test(body.secret)).toBe(true)
      secret = body.secret // Only from the normal UI request; never read the seed from the DB.
      const input = a.page.getByRole('textbox', { name: 'Код подтверждения 2FA', exact: true })
      await expect(input).toBeVisible()
      expect(version(a)).toBe(before)
      await session(copied) // Setup alone does not revoke a session.
      await input.fill(wrongFactor(secret))
      const wrong = responseFor(a.page, 'POST', '/api/auth/mfa/verify')
      await a.page.getByRole('button', { name: 'Подтвердить', exact: true }).click()
      expect((await wrong).status()).toBe(400)
      expect(factorState(a).enabled).toBe(false)
      expect(version(a)).toBe(before)
      await session(copied)
      const factor = await freshFactor(a, secret)
      await input.fill(factor.code)
      const enabled = responseFor(a.page, 'POST', '/api/auth/mfa/verify')
      await a.page.getByRole('button', { name: 'Подтвердить', exact: true }).click()
      expect((await enabled).status()).toBe(200)
      await expect(a.page.getByRole('status')).toContainText('MFA включена')
      expect(factorState(a).enabled).toBe(true)
      expect(Number(factorState(a).last_totp_step)).toBe(factor.step)
      expect(version(a)).toBe(before + 1)
      expect((await cookie(a.context)) !== oldCookie).toBe(true)
      await session(a)
      await session(copied, 401)
      await unchangedB()
    }, { timeout: STEP_TIMEOUT })

    await test.step('Session: credentials alone cannot bypass the real UI MFA factor', async () => {
      factorPeer = await peer()
      // A clean browser cookie jar, not a server logout (which would revoke A too).
      await factorPeer.context.clearCookies()
      const before = version(a)
      const password = await credentials(factorPeer, newPassword)
      expect(password.status()).toBe(200)
      const challenge = await password.json()
      expect(challenge.mfa_required).toBe(true)
      expect(typeof challenge.mfa_token === 'string' && challenge.mfa_token.length > 0).toBe(true)
      expect(password.headers()['set-cookie'] === undefined).toBe(true)
      pendingChallenge = challenge.mfa_token
      await session(factorPeer, 401)
      const input = factorPeer.page.locator('#login-mfa-code')
      await input.fill(wrongFactor(secret))
      const wrong = responseFor(factorPeer.page, 'POST', '/api/auth/login/mfa')
      await factorPeer.page.getByRole('button', { name: 'Подтвердить', exact: true }).click()
      expect((await wrong).status()).toBe(401)
      await session(factorPeer, 401)
      const factor = await freshFactor(a, secret)
      await input.fill(factor.code)
      const signedIn = responseFor(factorPeer.page, 'POST', '/api/auth/login/mfa')
      await factorPeer.page.getByRole('button', { name: 'Подтвердить', exact: true }).click()
      const result = await signedIn
      expect(result.status()).toBe(200)
      expect('access_token' in await result.json()).toBe(false)
      await factorPeer.page.waitForURL(WEB + '/dashboard', { timeout: 60_000 })
      await session(factorPeer)
      expect(Number(factorState(a).last_totp_step)).toBe(factor.step)
      expect(version(a)).toBe(before) // Login is not a global logout policy.
      await session(a)
      await unchangedB()
    }, { timeout: STEP_TIMEOUT })

    await test.step('Session: UI MFA disable refreshes A, rejects peers/challenge, preserves B isolation', async () => {
      const copied = await peer()
      const before = version(a), oldCookie = await cookie(a.context)
      await go(a.page, '/dashboard/account?tab=security')
      await a.page.getByRole('button', { name: 'Отключить', exact: true }).click()
      const input = a.page.getByRole('textbox', { name: 'Код для отключения 2FA', exact: true })
      await input.fill(wrongFactor(secret))
      const wrong = responseFor(a.page, 'DELETE', '/api/auth/mfa/disable')
      await a.page.getByRole('button', { name: 'Отключить', exact: true }).click()
      expect((await wrong).status()).toBe(400)
      expect(factorState(a).enabled).toBe(true)
      expect(version(a)).toBe(before)
      await session(copied)
      const factor = await freshFactor(a, secret)
      await input.fill(factor.code)
      const disabled = responseFor(a.page, 'DELETE', '/api/auth/mfa/disable')
      await a.page.getByRole('button', { name: 'Отключить', exact: true }).click()
      expect((await disabled).status()).toBe(200)
      await expect(a.page.getByRole('status')).toContainText('MFA отключена')
      expect(factorState(a).enabled).toBe(false)
      expect(Number(factorState(a).last_totp_step)).toBe(factor.step)
      expect(version(a)).toBe(before + 1)
      expect((await cookie(a.context)) !== oldCookie).toBe(true)
      await session(a)
      await session(copied, 401)
      expect(Boolean(factorPeer)).toBe(true)
      await session(factorPeer!, 401)
      const stale = await probe(a, '/api/auth/login/mfa', { mfa_token: pendingChallenge, code: '000000' })
      expect(stale.status()).toBe(401)
      expect((await stale.json()).detail).toBe('Недействительный MFA-токен')
      expect(stale.headers()['set-cookie'] === undefined).toBe(true)
      await passwordLogin(a, newPassword) // MFA really is disabled; no second-factor screen.
      await unchangedB()
      await isolation(a, b)
    }, { timeout: STEP_TIMEOUT })
  } finally {
    secret = ''; pendingChallenge = ''
    await Promise.all(peers.map(context => context.close()))
  }
}

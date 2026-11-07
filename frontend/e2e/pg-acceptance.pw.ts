import { expect, test } from '@playwright/test'
import { API, WEB, db, go, onboard, query, register, report, startBackend, stopBackend, type Seller } from './pg-helpers'
import { runSessionCases } from './pg-session-cases'
import type { PresentationCard, DecisionFeedItem } from '../lib/api'
import { marketplaceLabel } from '../lib/feedGrouping'

// Four composed real-browser tests. No marketplace, real mail, mock auth or DB writes.
// Reset mail is captured by the test-owned backend; this does NOT prove delivery.
test.describe.configure({ mode: 'serial' })
let a: Seller, b: Seller
test.beforeAll(async () => { await startBackend() })
test.afterAll(async () => {
  try { await stopBackend() }
  finally { await a?.context.close(); await b?.context.close() }
})

async function money(seller: Seller, revenue: number, profit: number) {
  await go(seller.page, `/dashboard/stores/${seller.storeId}`)
  await expect(seller.page.getByRole('heading', { name: 'Финансовый итог', exact: true })).toBeVisible()
  const response = await seller.context.request.get(`${API}/api/marketplace-stores/${seller.storeId}/finance-summary`)
  expect(response.status()).toBe(200)
  const summary = await response.json()
  expect(summary.revenue).toBe(revenue)
  expect(summary.net_profit).toBe(profit)
  const rubles = (amount: number) => new RegExp(`^${String(amount).split('').join('[\\s\\u00a0\\u202f]*')} ₽$`)
  await expect(seller.page.getByText(rubles(revenue))).toBeVisible()
  await expect(seller.page.getByText(rubles(profit))).toBeVisible()
  expect(query('SELECT COUNT(*) n FROM imported_finance_rows WHERE user_id=%s', [seller.id])[0].n).toBe(6)
}

async function openOwnProduct(seller: Seller) {
  await expect(seller.page.getByLabel('Загрузка', { exact: true })).toHaveCount(0)
  const summary = seller.page.locator('details > summary')
    .filter({ hasText: 'ART-1001' }).filter({ hasText: marketplaceLabel('wildberries') })
  if (await summary.count()) {
    await expect(summary).toHaveCount(1)
    const disclosure = summary.locator('xpath=..')
    if (await disclosure.getAttribute('open') === null) await summary.click()
    await expect(disclosure).toHaveAttribute('open', '')
  }
}

test('PostgreSQL: two real sellers import independent reports and see exact money and diagnosis', async ({ browser }) => {
  for (const tag of ['a', 'b']) {
    const seller = await test.step(`${tag}: register, verify own token, password login`, () => register(browser, tag))
    if (tag === 'a') a = seller; else b = seller
    await test.step(`${tag}: CSV-only cabinet through UI`, () => onboard(seller))
    await test.step(`${tag}: preview is read-only, confirmation writes six rows`, async () => {
      await seller.page.setInputFiles('input[type=file]', report(tag === 'a'))
      await seller.page.getByRole('button', { name: 'Проверить файл', exact: true }).click()
      await expect(seller.page.getByRole('heading', { name: 'Проверка файла', exact: true })).toBeVisible()
      expect(query('SELECT COUNT(*) n FROM imported_finance_rows WHERE user_id=%s', [seller.id])[0].n).toBe(0)
      const pending = seller.page.waitForResponse(r => r.url().includes('/confirm') && r.request().method() === 'POST')
      await seller.page.getByRole('button', { name: /Импортировать/i }).click()
      const response = await pending
      expect(response.status()).toBe(200)
      seller.importId = response.url().match(/import\/([^/]+)\/confirm/)![1]
      await expect(seller.page.getByText('Импорт завершён')).toBeVisible()
      await money(seller, tag === 'a' ? 900 : 1200, tag === 'a' ? 810 : 1110)
    })
  }
  await test.step('A collapse signal and B stable negative control', async () => {
    // Zero signals alone is not evidence: an error-isolated background producer
    // may never have completed. Bind BOTH successful runs to their confirmed import.
    for (const seller of [a, b]) {
      await expect.poll(() => query("SELECT COUNT(*) n FROM advisory_run r "
        + "JOIN import_records i ON i.id=%s AND i.user_id=r.user_id "
        + "WHERE r.user_id=%s AND r.producer_key='revenue_diagnosis' "
        + "AND r.triggered_by='import' AND r.status='ok' AND r.error IS NULL "
        + "AND r.finished_at IS NOT NULL AND r.started_at>=i.confirmed_at "
        + "AND r.finished_at>=r.started_at", [seller.importId, seller.id])[0].n,
      { timeout: 180_000, message: 'Own post-confirmation diagnosis completed successfully' }).toBeGreaterThan(0)
    }
    console.info('PG_DIAGNOSIS_BOTH_COMPLETED_AFTER_OWN_IMPORT')
    await go(a.page, '/dashboard')
    await expect(async () => {
      await a.page.reload()
      await expect(a.page.getByText(/ART-1001/).first()).toBeVisible({ timeout: 5_000 })
    }).toPass({ timeout: 180_000, intervals: [5_000] })
    const signals = query("SELECT user_id,COUNT(*) n FROM revenue_signal WHERE signal_key='revenue_collapse' GROUP BY user_id")
    expect(signals.find(row => row.user_id === a.id)?.n).toBe(1)
    expect(signals.find(row => row.user_id === b.id)?.n ?? 0).toBe(0)
    const items = (cards: PresentationCard[]): DecisionFeedItem[] => cards.flatMap(card => [
      ...card.items, ...card.recommendation_groups.flatMap(group => group.items),
    ])
    const presentation = async (seller: Seller) => {
      const response = await seller.context.request.get(`${API}/api/presentation/cards?limit=50`)
      expect(response.status()).toBe(200)
      return items((await response.json()).cards)
    }
    const aItems = await presentation(a)
    const [source] = query("SELECT id FROM revenue_signal WHERE user_id=%s AND signal_key='revenue_collapse'", [a.id])
    const collapse = aItems.find(item => item.source_table === 'revenue_signal' && item.source_id === source.id)
    expect(Boolean(collapse), 'A own revenue diagnosis reaches the actual presentation API').toBe(true)
    const headline = collapse!.what_happened || collapse!.title
    expect(typeof headline === 'string' && headline.length > 0).toBe(true)
    await openOwnProduct(a)
    await expect(a.page.getByText(headline!, { exact: true }).first()).toBeVisible()
    await go(b.page, '/dashboard')
    await openOwnProduct(b)
    const bItems = await presentation(b)
    // The SAME SKU can legitimately have B's own advice from other contours.
    // Source-row identities, unlike product/canonical item keys, are tenant-distinct.
    const aSources = new Set(aItems.map(item => `${item.source_table}:${item.source_id}`))
    expect(bItems.some(item => aSources.has(`${item.source_table}:${item.source_id}`))).toBe(false)
    expect(bItems.some(item => item.source_table === 'revenue_signal'
      && item.source_context?.signal_key === 'revenue_collapse')).toBe(false)
    await expect(b.page.getByText(headline!, { exact: true })).toHaveCount(0)
    console.info('PG_SHARED_SKU_NEGATIVE_CONTROL_NO_FOREIGN_SOURCE_OR_COLLAPSE')
  })
})

test('PostgreSQL: invalid and duplicate imports fail safely, tenants and mobile UI stay isolated', async () => {
  await test.step('foreign store and nonexistent store give the same refusal', async () => {
    const endpoint = (id: string) => `${API}/api/marketplace-stores/${id}/finance-summary`
    const foreign = await b.context.request.get(endpoint(a.storeId!))
    const missing = await b.context.request.get(endpoint('00000000-0000-0000-0000-000000000000'))
    expect(foreign.status()).toBe(404); expect(missing.status()).toBe(404)
    expect(await foreign.json()).toEqual(await missing.json())
    await go(b.page, `/dashboard/stores/${a.storeId}`)
    await expect(b.page.getByText(/Магазин не найден/)).toBeVisible()
    await expect(b.page.getByText('900 ₽', { exact: true })).toHaveCount(0)
  })
  await test.step('wrong extension never uploads', async () => {
    await go(a.page, `/dashboard/stores/${a.storeId}/import`)
    let uploads = 0
    const listener = (request: any) => { if (request.method() === 'POST' && request.url().includes('/api/import/upload')) uploads++ }
    a.page.on('request', listener)
    try {
      await a.page.setInputFiles('input[type=file]', { name: 'wrong.pdf', mimeType: 'application/pdf', buffer: Buffer.from('not CSV') })
      await expect(a.page.getByText('Поддерживаются только файлы .csv')).toBeVisible()
      expect(uploads).toBe(0)
    } finally { a.page.off('request', listener) }
  })
  await test.step('malformed CSV cannot confirm or change money', async () => {
    await a.page.setInputFiles('input[type=file]', { name: 'invalid.csv', mimeType: 'text/csv', buffer: Buffer.from('wrong_header\nnot_finance\n') })
    await a.page.getByRole('button', { name: 'Проверить файл', exact: true }).click()
    await expect(a.page.getByRole('alert').first()).toBeVisible()
    await expect(a.page.getByRole('button', { name: /Импортировать/i })).toBeDisabled()
    await money(a, 900, 810)
  })
  await test.step('same-file retry and already-confirmed ID never duplicate rows', async () => {
    await go(a.page, `/dashboard/stores/${a.storeId}/import`)
    await a.page.setInputFiles('input[type=file]', report(true))
    await a.page.getByRole('button', { name: 'Проверить файл', exact: true }).click()
    await expect(a.page.getByRole('heading', { name: 'Проверка файла', exact: true })).toBeVisible()
    const pending = a.page.waitForResponse(r => r.url().includes('/confirm') && r.request().method() === 'POST')
    await a.page.getByRole('button', { name: /Импортировать/i }).click()
    expect((await pending).status()).toBe(409)
    await expect(a.page.getByRole('alert').filter({ hasText: /Перед повторной попыткой проверьте историю/ })).toBeVisible()
    const replay = await a.context.request.post(`${API}/api/import/${a.importId}/confirm`, { headers: { Origin: WEB }, data: { mode: 'new' } })
    expect(replay.status()).toBe(400)
    await money(a, 900, 810)
  })
  await test.step('explicit injected read failure recovers without money writes', async () => {
    const pattern = `**/api/marketplace-stores/${a.storeId}/finance-summary`
    await a.page.route(pattern, route => route.fulfill({ status: 503, contentType: 'application/json',
      headers: { 'access-control-allow-origin': WEB, 'access-control-allow-credentials': 'true' },
      body: JSON.stringify({ detail: 'Controlled test-only read failure' }) }))
    try {
      await go(a.page, `/dashboard/stores/${a.storeId}`)
      await expect(a.page.getByRole('alert').filter({ hasText: /Не удалось загрузить финансовый итог/ })).toBeVisible()
    } finally { await a.page.unroute(pattern) }
    await a.page.getByRole('button', { name: 'Повторить', exact: true }).first().click()
    await expect(a.page.getByText('900 ₽', { exact: true })).toBeVisible()
    await money(a, 900, 810)
  })
  for (const width of [390, 320]) {
    for (const [name, route] of [['dashboard', '/dashboard'], ['finance', `/dashboard/stores/${a.storeId}`], ['import', `/dashboard/stores/${a.storeId}/import`]]) {
      await test.step(`${width}px ${name}: no overflow and keyboard-closeable menu`, async () => {
        await a.page.setViewportSize({ width, height: 844 }); await go(a.page, route)
        if (name === 'dashboard') await expect(a.page.getByText(/ART-1001/).first()).toBeVisible()
        if (name === 'finance') await expect(a.page.getByText('900 ₽', { exact: true })).toBeVisible()
        if (name === 'import') await expect(a.page.getByRole('radio', { name: /Финансы/ })).toBeVisible()
        expect(await a.page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
        await a.page.getByRole('button', { name: /^Открыть (меню|навигацию)$/ }).click()
        await expect(a.page.locator('.s-app.nav-open')).toHaveCount(1)
        await a.page.keyboard.press('Escape')
        await expect(a.page.locator('.s-app.nav-open')).toHaveCount(0)
      })
    }
  }
  await a.page.setViewportSize({ width: 1440, height: 1000 })
})

test('PostgreSQL: owned restart persists data and separate-target restore preserves all schema, data and sequences', async () => {
  await test.step('restart source backend and reread both tenants in the browser', async () => {
    await stopBackend(); await startBackend()
    await money(a, 900, 810); await money(b, 1200, 1110)
  })
  await test.step('quiesce writers; native dump; empty distinct target; exact comparison', async () => {
    await stopBackend()
    expect(db('backup').sourceUnchanged).toBe(true)
    const restored = db('restore')
    expect(restored.sourceUnchanged).toBe(true)
    expect(restored.schemaDataSequencesMatch).toBe(true)
    expect(restored.tables).toBeGreaterThan(0)
    console.info(`PG_RESTORE_EQUAL tables=${restored.tables} sequences=${restored.sequences} sourceUnchanged=true`)
  })
  await test.step('same application reads restored money through both authenticated browsers', async () => {
    await startBackend('pult_browser_restore')
    await money(a, 900, 810); await money(b, 1200, 1110)
    const refused = await b.context.request.get(`${API}/api/marketplace-stores/${a.storeId}/finance-summary`)
    expect(refused.status()).toBe(404)
    await stopBackend(); await startBackend()
  })
})

test('PostgreSQL: browser logout, reset and real MFA enforce existing session revocation rules', async ({ browser }) => {
  await runSessionCases(browser, a, b)
})

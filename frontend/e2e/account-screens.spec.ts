import path from 'path'
import { test, expect, type Page } from '@playwright/test'

// P6 visual capture — the Account page after the P1 migration. Cookie-gate + a seeded localStorage
// user (so the plan badge shows) + route-mocked mfa/telegram calls so the page renders fully. No
// backend rows, no product-code change — this only photographs the redesigned surface.
const OUT = path.join(__dirname, '..', 'e2e-screens')
const LABEL = process.env.SHOT_LABEL ?? 'after'
const CORS = { 'access-control-allow-origin': '*', 'content-type': 'application/json' }

async function setup(page: Page) {
  await page.context().addCookies([{ name: 'pult_token', value: 'e2e-shot-token', domain: '127.0.0.1', path: '/' }])
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-shot-token')
    localStorage.setItem('user', JSON.stringify({
      id: 'u1', name: 'Иван Продавец', email: 'seller@example.com',
      plan: 'master', subscription_status: 'active', is_verified: true,
    }))
  })
  await page.route('**/api/mfa/status', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify({ enabled: true }) }))
  await page.route('**/api/telegram/chat-id', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify({ chat_id: null }) }))
  await page.route('**/api/telegram/settings', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify({ daily_report: true, critical_alerts: true, weekly_summary: false }) }))
  await page.setViewportSize({ width: 1280, height: 1400 })
}

test('account — profile + settings', async ({ page }) => {
  await setup(page)
  await page.goto('/dashboard/account')
  await expect(page.getByText('seller@example.com').first()).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(500)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-account.png`), fullPage: true })
})

import path from 'path'
import { test, expect, type Page } from '@playwright/test'

// P5 visual capture — the responsive seller shell at three widths. Uses the same cookie-gate + a
// route-mocked empty reviews queue so the page renders without backend data; the shell itself is
// what is being photographed. Mobile is captured drawer-closed and drawer-open.
const OUT = path.join(__dirname, '..', 'e2e-screens')
const LABEL = process.env.SHOT_LABEL ?? 'after'
const CORS = { 'access-control-allow-origin': '*', 'content-type': 'application/json' }

async function open(page: Page, w: number, h: number) {
  await page.context().addCookies([{ name: 'pult_token', value: 'e2e-shot-token', domain: '127.0.0.1', path: '/' }])
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-shot-token'))
  await page.route('**/api/reviews/queue**', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }) }))
  await page.setViewportSize({ width: w, height: h })
  await page.goto('/dashboard/reviews')
  await expect(page.getByText('Отзывы').first()).toBeVisible({ timeout: 15_000 })
}

async function noHScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, 'no horizontal body scroll').toBeLessThanOrEqual(1)
}

test('shell — desktop 1280', async ({ page }) => {
  await open(page, 1280, 900)
  await noHScroll(page)
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-shell-desktop.png`), fullPage: false })
})

test('shell — tablet 768', async ({ page }) => {
  await open(page, 768, 1024)
  await noHScroll(page)
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-shell-tablet.png`), fullPage: false })
})

test('shell — mobile 375 drawer closed', async ({ page }) => {
  await open(page, 375, 780)
  await noHScroll(page)
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-shell-mobile-closed.png`), fullPage: false })
})

test('shell — mobile 375 drawer open', async ({ page }) => {
  await open(page, 375, 780)
  await page.getByRole('button', { name: /Открыть меню/ }).click()
  await expect(page.locator('.s-app.nav-open')).toBeVisible()
  await page.waitForTimeout(400)   // let the slide settle
  await noHScroll(page)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-shell-mobile-open.png`), fullPage: false })
})

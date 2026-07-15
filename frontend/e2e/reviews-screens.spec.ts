import path from 'path'
import { test, expect, type Page } from '@playwright/test'

// P3 visual capture — photographs the reviews workspace in a real browser. The E2E backend has no
// synced reviews (nothing connects a marketplace), so the queue/detail states are driven by route
// MOCKS: the page's own review API calls are intercepted and answered with fixture rows. This
// touches no backend and changes no product code — it only feeds the real page real-shaped data so
// the redesign can be photographed. Empty and loading states need no rows at all.
const OUT = path.join(__dirname, '..', 'e2e-screens')
const LABEL = process.env.SHOT_LABEL ?? 'after'

const CORS = { 'access-control-allow-origin': '*', 'content-type': 'application/json' }

function reviewRow(over: Record<string, unknown> = {}) {
  return {
    id: 'r1', product_id: 'p1', review_text: 'Отличный крем, беру уже третий раз. Спасибо!',
    author: 'Иван', rating: 5, response_text: null, status: 'pending', marketplace: 'wildberries',
    external_review_id: 'WB-1', review_created_at: null, safety_category: 'SAFE',
    manual_required_reason: null, published_at: null, failure_reason: null, publication_attempts: 0,
    created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z', state: 'New', ...over,
  }
}

const ROWS = [
  reviewRow({ id: 'r1', state: 'New', rating: 5, review_text: 'Отличный крем, беру уже третий раз. Спасибо!' }),
  reviewRow({ id: 'r2', state: 'NeedsAttention', rating: 2, safety_category: 'RISK',
             manual_required_reason: 'Возможная жалоба — нужна ручная проверка перед ответом',
             review_text: 'Пришло с повреждённой упаковкой, недоволен.' }),
  reviewRow({ id: 'r3', state: 'Drafted', rating: 4, response_text: 'Спасибо за отзыв!',
             review_text: 'В целом хорошо, но доставка задержалась.' }),
  reviewRow({ id: 'r4', state: 'Published', rating: 5, response_text: 'Благодарим за оценку!',
             review_text: 'Всё супер, рекомендую.' }),
  reviewRow({ id: 'r5', state: 'Failed', rating: 3, failure_reason: 'TIMEOUT', publication_attempts: 2,
             review_text: 'Нормально, но цена высоковата.' }),
]

async function auth(page: Page) {
  // The middleware guard redirects to /login unless a `pult_token` cookie is present (it checks
  // presence only, never validates), so a placeholder cookie is enough to reach the page. The
  // localStorage token keeps client-side calls in their logged-in branch. All review data is mocked.
  await page.context().addCookies([
    { name: 'pult_token', value: 'e2e-shot-token', domain: '127.0.0.1', path: '/' },
  ])
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-shot-token'))
  await page.setViewportSize({ width: 1280, height: 1200 })
}

test('reviews — queue + detail (populated)', async ({ page }) => {
  await auth(page)
  await page.route('**/api/reviews/queue**', r => r.fulfill({
    status: 200, headers: CORS,
    body: JSON.stringify({ items: ROWS, total: ROWS.length, limit: 50, offset: 0 }),
  }))
  await page.route('**/api/reviews/**/history', r => r.fulfill({
    status: 200, headers: CORS, body: JSON.stringify({ review_id: 'r1', entries: [] }),
  }))

  await page.goto('/dashboard/reviews')
  await expect(page.getByText('Отличный крем, беру уже третий раз. Спасибо!')).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-reviews-queue.png`), fullPage: true })

  // open the RISK review → detail with the human-review safety reason
  await page.getByText('Пришло с повреждённой упаковкой, недоволен.').click()
  await expect(page.getByText(/нужна ручная проверка/)).toBeVisible()
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-reviews-detail.png`), fullPage: true })
})

test('reviews — honest empty state', async ({ page }) => {
  await auth(page)
  await page.route('**/api/reviews/queue**', r => r.fulfill({
    status: 200, headers: CORS, body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }),
  }))
  await page.goto('/dashboard/reviews')
  await expect(page.getByText('Отзывов пока нет')).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-reviews-empty.png`), fullPage: true })
})

test('reviews — loading skeleton', async ({ page }) => {
  await auth(page)
  // never fulfill → the page stays in its loading (skeleton) state
  await page.route('**/api/reviews/queue**', async () => { /* hang */ })
  await page.goto('/dashboard/reviews')
  await expect(page.locator('.pult-skeleton').first()).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-reviews-loading.png`), fullPage: true })
})

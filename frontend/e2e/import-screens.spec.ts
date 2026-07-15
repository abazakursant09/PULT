import path from 'path'
import { test, expect, type Page } from '@playwright/test'

// P4 visual capture — photographs the import flow in a real browser. The stage data is driven by
// route MOCKS of the page's own csvImport calls, so no backend rows are created and no product
// code changes. Empty history → the finance-first path, matching a genuine first import.
const OUT = path.join(__dirname, '..', 'e2e-screens')
const LABEL = process.env.SHOT_LABEL ?? 'after'
const CORS = { 'access-control-allow-origin': '*', 'content-type': 'application/json' }

const PREVIEW = {
  import_id: 'imp-1', marketplace: 'wb', import_type: 'finance',
  total_rows: 120, valid_rows: 118, skipped_rows: 2,
  headers: ['Дата', 'Выручка', 'Комиссия'],
  mapped_columns: { date: 'Дата', revenue: 'Выручка', commission: 'Комиссия' },
  unmapped_required: [],
  preview_rows: [
    { 'Дата': '14.07.2026', 'Выручка': '12 000', 'Комиссия': '1 800' },
    { 'Дата': '13.07.2026', 'Выручка': '9 500', 'Комиссия': '1 420' },
  ],
  warnings: ['Строка 44: пустое значение комиссии'],
  errors: [], duplicate_import_id: null, duplicate_date: null,
}

async function auth(page: Page) {
  await page.context().addCookies([{ name: 'pult_token', value: 'e2e-shot-token', domain: '127.0.0.1', path: '/' }])
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-shot-token'))
  await page.setViewportSize({ width: 1280, height: 1200 })
}

async function history(page: Page) {
  await page.route('**/api/import/history**', r => r.fulfill({ status: 200, headers: CORS, body: '[]' }))
}
async function pickFile(page: Page) {
  await page.setInputFiles('input[type="file"]', {
    name: 'finance.csv', mimeType: 'text/csv', buffer: Buffer.from('Дата,Выручка,Комиссия\n14.07.2026,12000,1800\n'),
  })
}

test('import — upload stage', async ({ page }) => {
  await auth(page); await history(page)
  await page.goto('/dashboard/import')
  await expect(page.getByText(/Перетащите CSV сюда/)).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-import-upload.png`), fullPage: true })
})

test('import — preview stage', async ({ page }) => {
  await auth(page); await history(page)
  await page.route('**/api/import/upload', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify(PREVIEW) }))
  await page.goto('/dashboard/import')
  await pickFile(page)
  await page.getByRole('button', { name: /Загрузить и проверить/i }).click()
  await expect(page.getByText('КОРРЕКТНЫХ')).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-import-preview.png`), fullPage: true })
})

test('import — importing stage', async ({ page }) => {
  await auth(page); await history(page)
  await page.route('**/api/import/upload', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify(PREVIEW) }))
  await page.route('**/api/import/*/confirm', async () => { /* hang → importing stays */ })
  await page.goto('/dashboard/import')
  await pickFile(page)
  await page.getByRole('button', { name: /Загрузить и проверить/i }).click()
  await page.getByRole('button', { name: /Импортировать/i }).click()
  await expect(page.getByText('Импортируем данные…')).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-import-importing.png`), fullPage: true })
})

test('import — done stage', async ({ page }) => {
  await auth(page); await history(page)
  await page.route('**/api/import/upload', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify(PREVIEW) }))
  await page.route('**/api/import/*/confirm', r => r.fulfill({
    status: 200, headers: CORS, body: JSON.stringify({ imported_count: 118, skipped_count: 2 }),
  }))
  await page.goto('/dashboard/import')
  await pickFile(page)
  await page.getByRole('button', { name: /Загрузить и проверить/i }).click()
  await page.getByRole('button', { name: /Импортировать/i }).click()
  await expect(page.getByText('Импорт завершён')).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-import-done.png`), fullPage: true })
})

test('import — error stage', async ({ page }) => {
  await auth(page); await history(page)
  await page.route('**/api/import/upload', r => r.fulfill({ status: 200, headers: CORS, body: JSON.stringify(PREVIEW) }))
  await page.route('**/api/import/*/confirm', r => r.fulfill({
    status: 500, headers: CORS, body: JSON.stringify({ detail: 'Внутренняя ошибка сервера' }),
  }))
  await page.goto('/dashboard/import')
  await pickFile(page)
  await page.getByRole('button', { name: /Загрузить и проверить/i }).click()
  await page.getByRole('button', { name: /Импортировать/i }).click()
  await expect(page.getByRole('button', { name: /Загрузить снова/i })).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${LABEL}-import-error.png`), fullPage: true })
})

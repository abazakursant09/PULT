import { execFileSync } from 'child_process'
import { rmSync } from 'fs'
import path from 'path'
import { expect, test, type Page } from '@playwright/test'
import { writeCollapseReport } from './fixture'

// THE Advisory MVP path, in a real browser, against a real backend:
//
//   register → verify email → log in → upload a report → confirm → diagnosis card
//
// Everything upstream — 15 producers, 8 diagnosis contours, the whole ingestion spine —
// exists so that the last step happens. Nothing proved it end to end: the backend's 3494
// tests never open a browser, and the Vitest suite mocks the API away. This is the only test
// in the repository that proves a seller can actually get a diagnosis out of PULT.
//
// It touches no marketplace, needs no credentials, and runs on a throwaway database.

const DB = process.env.E2E_DB_PATH!
const TMP = path.dirname(DB)

/** Read the verification token from the disposable test database.
 *
 * Registration deliberately does NOT return the link — P7.1 removed it, because returning it
 * was an account-takeover hole — and no SMTP is configured here, so the mail is only logged.
 * Rather than mock the product's auth or add a test-only backdoor to it, we take the token
 * out of our own throwaway database and then open the very link a real seller opens. Python's
 * stdlib sqlite3 does the read, so this needs no extra Node dependency.
 */
function verificationToken(email: string): string {
  const py = [
    'import sqlite3, sys',
    `c = sqlite3.connect(r"${DB}")`,
    'r = c.execute("SELECT verification_token FROM users WHERE email = ?", (sys.argv[1],)).fetchone()',
    'print(r[0] if r and r[0] else "")',
  ].join('\n')
  return execFileSync('python', ['-c', py, email], { encoding: 'utf-8' }).trim()
}

/** Solve the anti-bot question by reading it, exactly as a person does. */
async function solveCaptcha(page: Page) {
  const question = (await page.locator('span', { hasText: /=\s*\?/ }).first().innerText()).trim()
  const m = question.match(/(\d+)\s*([+−-])\s*(\d+)/)
  if (!m) throw new Error(`unreadable anti-bot question: ${question}`)
  const [a, op, b] = [Number(m[1]), m[2], Number(m[3])]
  const answer = op === '+' ? a + b : a - b
  await page.getByPlaceholder('Ответ...').fill(String(answer))
}

test('a seller registers, uploads a report, and sees a real diagnosis', async ({ page }) => {
  // Unique per run: reruns never collide, and no real person's data is used.
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`
  const password = 'E2ePassw0rd!'

  // ── 1. Register ───────────────────────────────────────────────────────────
  await page.goto('/register')

  // The cookie banner is fixed at z-9999 and swallows clicks on the form beneath it. A real
  // seller dismisses it before they can submit anything, so the test does the same.
  await page.getByRole('button', { name: /Принять все/i }).click()

  await page.getByPlaceholder('Иван Петров').fill('E2E Seller')
  await page.locator('input[type="email"]').first().fill(email)
  const passwords = page.locator('input[type="password"]')
  await passwords.nth(0).fill(password)
  await passwords.nth(1).fill(password)

  await solveCaptcha(page)

  // The consents must be ticked by clicking the TEXT, not the checkbox square.
  //
  // Clicking the square does nothing at all: <label> forwards a click to the <button> it
  // wraps (a button is a labelable element), so the button's own handler and the label's
  // handler both fire and the state toggles twice, back to false. This test found that — a
  // real defect a unit test cannot see — and it is reported, not patched around here.
  const consents = page.locator('label').filter({ hasText: /Я даю согласие|Я принимаю условия/ })
  await consents.nth(0).click({ position: { x: 60, y: 6 } })
  await consents.nth(1).click({ position: { x: 60, y: 6 } })

  const submit = page.locator('button[type="submit"]')
  await expect(submit).toBeEnabled()
  await submit.click()

  // Registration does not sign you in: the account is created unverified, by design.
  await expect(page.getByText('Проверьте почту')).toBeVisible({ timeout: 30_000 })

  // ── 2. Verify the email by opening the link the seller would be sent ───────
  const token = verificationToken(email)
  expect(token, 'registration must mint a verification token').toBeTruthy()
  await page.goto(`/verify-email?token=${token}`)
  await page.waitForTimeout(2_000)          // the page exchanges the token for a session

  // ── 3. Log in ─────────────────────────────────────────────────────────────
  await page.goto('/login')
  await page.locator('input[type="email"]').first().fill(email)
  await page.locator('input[type="password"]').first().fill(password)
  await page.locator('button[type="submit"]').first().click()
  await page.waitForURL(/\/dashboard/, { timeout: 30_000 })

  // ── 4. First run: no data, and the seller is told the truth about it ───────
  // This is the screen that used to promise a marketplace sync that never comes.
  await expect(page.getByText('Нет данных для анализа')).toBeVisible({ timeout: 30_000 })

  // ── 5. Follow the only road that exists ───────────────────────────────────
  await page.getByRole('link', { name: /Загрузить отчёт/i }).click()
  await page.waitForURL(/\/dashboard\/import/)

  // ── 6-7. Choose the report and say what it is ─────────────────────────────
  await page.setInputFiles('input[type="file"]', writeCollapseReport(TMP))
  await page.getByText('Wildberries').first().click()
  await page.getByText(/Финансы|Финансовый/i).first().click()

  // ── 8. Upload → the backend really parses the file ────────────────────────
  await page.getByRole('button', { name: /Загрузить и проверить/i }).click()
  await expect(page.getByText('КОРРЕКТНЫХ')).toBeVisible({ timeout: 60_000 })

  // ── 9. Confirm → the rows are really persisted ────────────────────────────
  await page.getByRole('button', { name: /Импортировать/i }).click()
  await expect(page.getByText('Импорт завершён')).toBeVisible({ timeout: 60_000 })

  // ── 10-11. Back to the dashboard ──────────────────────────────────────────
  await page.getByRole('button', { name: /Перейти в Пульт/i }).click()
  await page.waitForURL(/\/dashboard$/)

  // ── 12. The diagnosis itself ──────────────────────────────────────────────
  //
  // The seller only becomes visible to the Advisory Runtime once finance rows exist
  // (_active_user_ids reads ImportedFinanceRow), so the producers run on the first scheduler
  // tick AFTER this import — within ~60s. We poll for the card instead of sleeping blindly.
  //
  // The fixture makes the verdict inevitable rather than likely: revenue 1000×3 → 300×3 gives
  // windows (3000, 900) — a ratio of 0.30 against COLLAPSE_RATIO 0.5, with cv 0.0. The revenue
  // producer has no choice but to classify it a "collapse".
  await expect(async () => {
    await page.reload()
    await expect(page.getByText(/ART-1001/).first()).toBeVisible({ timeout: 5_000 })
  }).toPass({ timeout: 180_000, intervals: [5_000] })

  // Not a URL and not an empty shell — actual diagnosis content, rendered in a browser.
  await expect(page.getByText(/ART-1001/).first()).toBeVisible()
  await expect(page.getByText('Нет данных для анализа')).toHaveCount(0)
})

test.afterAll(() => {
  // Disposable state beats manual cleanup. Best-effort: on Windows the backend may still
  // hold the file open, and the directory is git-ignored and wiped at the start of each run.
  try { rmSync(TMP, { recursive: true, force: true }) } catch { /* wiped on next run */ }
})

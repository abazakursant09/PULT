import { spawn, execFileSync, type ChildProcess } from 'child_process'
import { randomBytes } from 'crypto'
import { openSync, closeSync } from 'fs'
import path from 'path'
import { expect, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { stopOwnedProcess } from './pg-process'

export const API = 'http://127.0.0.1:8097'
export const WEB = 'http://127.0.0.1:3097'
export const PASSWORD = 'Browser-Only-2026!'
export type Seller = { context: BrowserContext; page: Page; email: string; id: string; storeId?: string; importId?: string }
const root = process.env.PULT_E2E_PG_ROOT!
const python = process.env.PULT_E2E_PYTHON || 'python'
const secret = randomBytes(32).toString('hex')
let child: ChildProcess | undefined
let log: number | undefined
let starts = 0

export function db(action: string, data?: unknown): any {
  return JSON.parse(execFileSync(python, [path.join(__dirname, 'pg-database.py'), action,
    ...(data === undefined ? [] : [JSON.stringify(data)])], { encoding: 'utf8', timeout: 60_000 }))
}
export function query(sql: string, params: unknown[] = []): any[] { return db('query', { sql, params }) }
export async function stopBackend() {
  if (!child) return
  const owned = child
  try { await stopOwnedProcess(owned) }
  finally {
    child = undefined
    if (log !== undefined) closeSync(log)
    log = undefined
  }
  await expect.poll(async () => { try { await fetch(API + '/api/health'); return false } catch { return true } }).toBe(true)
}
export async function startBackend(database = 'pult_browser_source') {
  expect(child, 'Only this test owns and restarts its backend').toBeUndefined()
  expect(['pult_browser_source', 'pult_browser_restore']).toContain(database)
  try { await fetch(API + '/api/health'); throw new Error('Backend port is already occupied') }
  catch (error) { if ((error as Error).message === 'Backend port is already occupied') throw error }
  log = openSync(path.join(root, `backend-${++starts}.log`), 'wx', 0o600)
  child = spawn(python, [path.join(__dirname, 'pg-backend.py')], {
    env: { ...process.env, PULT_E2E_DATABASE: database, PULT_E2E_SESSION_SECRET: secret },
    stdio: ['ignore', log, log], windowsHide: true,
  })
  child.on('error', () => { /* health poll reports a bounded startup failure */ })
  await expect.poll(async () => {
    if (child?.exitCode !== null) return `exit:${child?.exitCode}`
    try { return (await fetch(API + '/api/health')).status } catch { return 'starting' }
  }, { timeout: 90_000 }).toBe(200)
}
export async function notice(page: Page) {
  const button = page.getByRole('button', { name: 'Понятно', exact: true })
  if (await button.isVisible()) { await button.click(); await expect(button).not.toBeVisible() }
}
export async function go(page: Page, route: string) {
  await page.goto(WEB + route, { waitUntil: 'networkidle', timeout: 120_000 })
  await notice(page)
}
export async function login(seller: Seller, password = PASSWORD) {
  await go(seller.page, '/login')
  await seller.page.locator('input[type=email]').first().fill(seller.email)
  await seller.page.locator('input[type=password]').first().fill(password)
  await seller.page.locator('button[type=submit]').first().click()
}
export async function register(browser: Browser, tag: string): Promise<Seller> {
  const context = await browser.newContext({ serviceWorkers: 'block' })
  await context.route('**/*', route => {
    const origin = new URL(route.request().url()).origin
    return [WEB, API].includes(origin) ? route.continue() : route.abort()
  })
  const page = await context.newPage()
  page.setDefaultTimeout(20_000)
  console.info(`PG_REGISTER ${tag}: form`)
  const email = `browser-${tag}-${randomBytes(12).toString('hex')}@example.com`
  await go(page, '/register')
  await page.getByPlaceholder('Иван Петров').fill(`Browser ${tag}`)
  await page.locator('input[type=email]').first().fill(email)
  await page.locator('input[type=password]').nth(0).fill(PASSWORD)
  await page.locator('input[type=password]').nth(1).fill(PASSWORD)
  const question = await page.locator('span', { hasText: /=\s*\?/ }).first().innerText()
  const match = question.match(/(\d+)\s*([+−-])\s*(\d+)/)!
  expect(match).toBeTruthy()
  await page.getByPlaceholder('Ответ...').fill(String(match[2] === '+' ? +match[1] + +match[3] : +match[1] - +match[3]))
  await page.locator('label[for=consent-privacy] span[aria-hidden=true]').click()
  await page.locator('label[for=consent-terms] span[aria-hidden=true]').click()
  await page.locator('button[type=submit]').click()
  await expect(page.getByRole('heading', { name: 'Аккаунт создан', exact: true })).toBeVisible()
  await expect(page.getByText('Не удалось отправить письмо. Проверьте адрес и попробуйте ещё раз.')).toBeVisible()
  const [user] = query('SELECT id,verification_token FROM users WHERE email=%s', [email])
  expect(user.verification_token).toBeTruthy()
  const seller: Seller = { context, page, email, id: user.id }
  console.info(`PG_REGISTER ${tag}: own-token verification`)
  await go(page, '/verify-email?token=' + encodeURIComponent(user.verification_token))
  await expect.poll(() => query('SELECT is_verified FROM users WHERE id=%s', [user.id])[0].is_verified).toBe(true)
  await login(seller)
  await page.waitForURL(/\/dashboard/, { timeout: 60_000 })
  await expect(page.getByText('Нет данных для анализа')).toBeVisible()
  await notice(page)
  console.info(`PG_REGISTER ${tag}: password login complete`)
  return seller
}
export async function onboard(seller: Seller) {
  const page = seller.page
  console.info('PG_ONBOARD: CSV cabinet')
  await page.getByRole('link', { name: /Загрузить отчёт/i }).click()
  await expect(page.getByText('Нет ни одного активного магазина.')).toBeVisible()
  await notice(page)
  await page.getByRole('link', { name: 'Перейти к магазинам', exact: true }).click()
  await page.waitForURL(/\/dashboard\/stores/)
  await notice(page)
  await page.getByRole('button', { name: 'Добавить кабинет', exact: true }).click()
  const cabinet = page.getByRole('dialog', { name: 'Добавить кабинет', exact: true })
  await cabinet.getByLabel('Маркетплейс', { exact: true }).selectOption('wildberries')
  await cabinet.getByLabel('Название кабинета', { exact: true }).fill('Browser CSV cabinet')
  await cabinet.getByRole('radio', { name: /Работать через CSV/ }).check()
  await notice(page)
  await cabinet.getByRole('button', { name: 'Добавить кабинет', exact: true }).click()
  await expect(cabinet).not.toBeVisible()
  await page.getByRole('link', { name: 'Импорт данных', exact: true }).click()
  await page.getByRole('link', { name: 'Выбрать', exact: true }).click()
  await page.waitForURL(/\/dashboard\/stores\/[^/]+\/import$/)
  seller.storeId = page.url().match(/stores\/([^/]+)/)![1]
}
export function report(drop: boolean) {
  const lines = ['Дата,Артикул WB,Название,Выручка,Комиссия,Логистика,Реклама,Чистая прибыль,Количество']
  ;(drop ? [200, 200, 200, 100, 100, 100] : [200, 200, 200, 200, 200, 200]).forEach((amount, i) => {
    const d = new Date(); d.setDate(d.getDate() - (6 - i))
    lines.push(`${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()},ART-1001,Учебный товар,${amount},10,5,0,${amount - 15},1`)
  })
  return { name: drop ? 'drop.csv' : 'stable.csv', mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n') + '\n') }
}

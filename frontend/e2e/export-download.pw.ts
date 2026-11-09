import { test, expect } from '@playwright/test'
import { readFile } from 'fs/promises'

test('real browser downloads only owner imported rows and saved analysis', async ({ page }) => {
  await page.goto('/login')
  await page.getByRole('button', { name: 'Понятно', exact: true }).click()
  await page.locator('input[type="email"]').first().fill('export-owner@example.com')
  await page.locator('input[type="password"]').first().fill('SyntheticExport123!')
  await page.locator('button[type="submit"]').first().click()
  await page.waitForURL(/\/dashboard/, { timeout: 60_000 })
  await page.goto('/dashboard/data')
  for (const [label, filename, scope, marker] of [
    ['Скачать импортированные данные', 'pult-imported-data.json', 'imported_rows', 'owner-SKU'],
    ['Скачать сохранённую аналитику', 'pult-saved-analysis.json', 'saved_analysis', 'owner-saved-analysis'],
  ]) {
    const event = page.waitForEvent('download', { timeout: 30_000 })
    const responseEvent = page.waitForResponse(r => r.url().includes(`/api/data/export/${scope === 'imported_rows' ? 'imports' : 'analysis'}`))
    await page.getByRole('button', { name: label, exact: true }).click()
    const response = await responseEvent
    expect(response.status(), `export response: ${await response.text()}`).toBe(200)
    const download = await event
    expect(download.suggestedFilename()).toBe(filename)
    expect(await download.failure()).toBeNull()
    const file = await download.path()
    expect(file).toBeTruthy()
    const text = await readFile(file!, 'utf8')
    expect(JSON.parse(text).scope).toBe(scope)
    expect(text).toContain(marker)
    expect(text).not.toContain('other-SKU')
    expect(text).not.toContain('other-saved-analysis')
    expect(text).not.toContain('hashed_password')
    expect(text).not.toContain('secret_enc')
  }
  await page.screenshot({ path: 'test-results/export-download.png', fullPage: true })
})

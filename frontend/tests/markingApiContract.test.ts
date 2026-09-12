import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const ROOT = join(__dirname, '..')
const read = (...path: string[]) => readFileSync(join(ROOT, ...path), 'utf8')

describe('marking API contract', () => {
  const page = read('app', 'dashboard', 'marking', 'page.tsx')
  const client = read('lib', 'api.ts')

  it('uses the shared API origin rather than the frontend-relative /api path', () => {
    expect(page).toContain('api.marking.scan()')
    expect(page).toContain('api.marking.check(checkCat.trim())')
    expect(page).not.toMatch(/fetch\(['`]\/api\/marking/)
    expect(client).toContain("scan: () => req<MarkingProductStatus[]>('/api/marking/scan')")
  })

  it('does not present a failed scan or category check as a successful empty result', () => {
    expect(page).toContain('setScanError(true)')
    expect(page).toContain('setCheckError(true)')
    expect(page).toContain('Не удалось загрузить товары')
    expect(page).toContain('Не удалось проверить категорию')
    expect(page).toContain('!loading && !scanError')
  })
})

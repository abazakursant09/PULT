import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const ROOT = join(__dirname, '..')
const APP = join(ROOT, 'app')

const PRODUCT_NAME_SURFACES = {
  'layout.tsx': { token: "title: 'Пульт — операционная система селлера'", count: 1 },
  'dashboard/security/page.tsx': { token: 'название «Пульт»', count: 1 },
  'startup/[step]/page.tsx': { token: 'Пульт берёт всё на автопилот', count: 1 },
  'startup/calculator/page.tsx': { token: 'Начать с Пультом', count: 2 },
  'support/page.tsx': { token: 'Пульт', count: 2 },
} as const

function productNameDefects(source: string): string[] {
  const defects: string[] = []
  if (source.includes('Бизнес-Пульт')) defects.push('retired-business-prefix')
  if (source.includes('Пульт OS')) defects.push('retired-os-suffix')
  return defects
}

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? filesUnder(path) : [path]
  })
}

describe('product positioning stays specific to the seller operating system', () => {
  it('pins the approved root metadata promise', () => {
    const layout = readFileSync(join(APP, 'layout.tsx'), 'utf8')

    expect(layout).toContain("title: 'Пульт — операционная система селлера'")
    expect(layout).toContain(
      "description: 'Спрос, продажи, маржа, остатки и ежедневные решения для продавцов на маркетплейсах — в едином операционном контуре.'",
    )
  })

  it('uses the same positioning on login and removes the generic legacy claim', () => {
    const login = readFileSync(join(APP, 'login', 'page.tsx'), 'utf8')
    const appSource = filesUnder(APP)
      .filter(path => /\.(ts|tsx)$/.test(path))
      .map(path => readFileSync(path, 'utf8'))
      .join('\n')

    expect(login).toContain('Операционная система селлера')
    expect(appSource).not.toContain('Центр управления бизнесом')
  })

  it('uses the canonical product name on every non-legal product surface', () => {
    for (const [path, contract] of Object.entries(PRODUCT_NAME_SURFACES)) {
      const source = readFileSync(join(APP, path), 'utf8')
      expect(productNameDefects(source), path).toEqual([])
      expect(source.split(contract.token).length - 1, path).toBe(contract.count)
    }
  })

  it.each([
    ['business prefix', 'Бизнес-Пульт'],
    ['OS suffix', 'Пульт OS'],
  ])('fails closed when the retired %s returns', (_label, retiredName) => {
    expect(productNameDefects(`visible product label: ${retiredName}`)).not.toEqual([])
  })

  it('fails closed when the canonical name is removed from a product surface', () => {
    const { token, count } = PRODUCT_NAME_SURFACES['support/page.tsx']
    const source = readFileSync(join(APP, 'support/page.tsx'), 'utf8').replace(token, 'Сервис')
    expect(source.split(token).length - 1).not.toBe(count)
  })
})

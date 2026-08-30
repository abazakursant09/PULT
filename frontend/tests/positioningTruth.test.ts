import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const ROOT = join(__dirname, '..')
const APP = join(ROOT, 'app')

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? filesUnder(path) : [path]
  })
}

describe('product positioning stays specific to the seller operating system', () => {
  it('pins the approved root metadata promise', () => {
    const layout = readFileSync(join(APP, 'layout.tsx'), 'utf8')

    expect(layout).toContain("title: 'Пульт OS — операционная система селлера'")
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
})

import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// B1 + B2 — marketing honesty. The landing must not imply Auto Reviews works on every marketplace
// (review reply is Wildberries-only), and the support FAQ must not describe a marketplace-API
// connection as the current or shipped product model (the product is CSV import, no API keys).
// Static source guards — marketing copy, no render needed.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')

describe('B1 — landing scopes Auto Reviews to Wildberries', () => {
  const src = read('app', 'page.tsx')

  it('the autoreply feature names Wildberries explicitly', () => {
    // the one autoreply mention must carry the WB scope, not a bare cross-marketplace "Автоответы"
    expect(src).toMatch(/Автоответы на отзывы — Wildberries/)
  })

  it('no bare unscoped "Автоответы" label remains', () => {
    expect(src).not.toMatch(/title:\s*'Автоответы'\s*}/)
  })
})

describe('B2 — support FAQ reflects the shipped CSV product, not an API connection', () => {
  const src = read('app', 'support', 'page.tsx')

  it('does not describe a demo-mode / API-connection model as current or upcoming', () => {
    expect(src).not.toMatch(/демо-режим/)
    expect(src).not.toMatch(/Подключение через API маркетплейсов/)
    expect(src).not.toMatch(/Подключение Wildberries API/)
  })

  it('states the real CSV-import, no-API-key flow', () => {
    expect(src).toMatch(/API-ключи не нужны/)
    expect(src).toMatch(/раздел[е"].*Импорт данных|«Импорт данных»/)
  })
})

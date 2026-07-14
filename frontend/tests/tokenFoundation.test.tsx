import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// P0 — one token source of truth. These guards fail the day the palette forks again: the seller
// shell must NOT redefine the core palette (it inherits from :root), and the shared UI primitives
// must reference tokens, not raw hex.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')

describe('unified token foundation', () => {
  it('defines the canonical palette once, in globals :root', () => {
    const g = read('styles', 'globals.css')
    for (const v of ['--bg:', '--surface:', '--elevated:', '--text:', '--violet:',
                     '--success:', '--danger:', '--r-sm:', '--dur:', '--ease:']) {
      expect(g, `globals :root must define ${v}`).toContain(v)
    }
    // the seller-shell aliases resolve to the same tokens
    for (const alias of ['--panel:', '--tx:', '--gain:', '--loss:', '--r:']) {
      expect(g).toContain(alias)
    }
  })

  it('seller.css no longer redefines the palette — it inherits', () => {
    // The .s-app block must not carry its own --bg/--panel/--tx hardcoded hex declarations.
    const s = read('styles', 'seller.css')
    const sApp = s.slice(s.indexOf('.s-app{'), s.indexOf('}', s.indexOf('.s-app{')))
    expect(sApp).not.toMatch(/--panel\s*:\s*#/)
    expect(sApp).not.toMatch(/--tx\s*:\s*#/)
    expect(sApp).not.toMatch(/--bg\s*:\s*#/)
  })

  it('shared UI primitives reference tokens, not raw hex', () => {
    for (const f of [['components', 'ui', 'button.tsx'], ['components', 'ui', 'card.tsx']]) {
      const src = read(...f)
      expect(src, `${f.join('/')} must not hardcode hex`).not.toMatch(/#[0-9A-Fa-f]{6}/)
      expect(src).toMatch(/var\(--/)
    }
  })

  it('there is one background token value, not six', () => {
    // The canonical --bg is defined exactly once in :root (light theme override excluded here by
    // checking the :root block only).
    const g = read('styles', 'globals.css')
    const root = g.slice(g.indexOf(':root {'), g.indexOf('[data-theme="light"]'))
    const bgDefs = (root.match(/--bg:\s*#[0-9A-Fa-f]{6}/g) || []).length
    expect(bgDefs).toBe(1)
  })
})

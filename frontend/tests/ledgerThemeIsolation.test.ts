import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// The Executive Ledger is the language of the STORE screens, and of nothing else.
//
// PULT's other pages are a dark, violet product. Loading the ledger's fonts globally or letting
// its stylesheet escape would restyle all of them — a change nobody asked for, arriving as a side
// effect of a store screen. These tests make that impossible to do by accident.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')

const LEDGER_ROUTES = [
  join('app', 'dashboard', 'stores'),
  join('app', 'dashboard', 'imports'),
  join('app', 'dashboard', 'import'),
  join('components', 'stores'),
]

function sourcesUnder(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.next' || entry.startsWith('.')) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...sourcesUnder(full))
    else if (/\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

const ALL = [...sourcesUnder(join(ROOT, 'app')), ...sourcesUnder(join(ROOT, 'components'))]

describe('the ledger theme stays on the store screens', () => {
  it('adds no font to the global layout', () => {
    const layout = read('app', 'layout.tsx')
    expect(layout).not.toMatch(/Source_Serif/)
    expect(layout).not.toMatch(/IBM_Plex/)
    expect(layout).not.toMatch(/ledger/i)
  })

  it('leaves the global stylesheet untouched by the ledger', () => {
    const globals = read('styles', 'globals.css')
    expect(globals).not.toMatch(/ledger/i)
    expect(globals).not.toMatch(/font-ledger/)
  })

  it('defines the ledger palette only inside its own scope', () => {
    const css = read('styles', 'ledger.css')
    // every rule is under `.ledger` — no :root, no bare element selector
    expect(css).not.toMatch(/^\s*:root/m)
    expect(css).not.toMatch(/^\s*(body|html)\s*\{/m)
    for (const line of css.split('\n')) {
      const selector = line.match(/^([^@\s/][^{]*)\{/)
      if (!selector) continue
      expect(selector[1], `selector must be scoped: ${selector[1].trim()}`).toMatch(/\.ledger/)
    }
  })

  it('is imported and applied only by the store routes', () => {
    const importers = ALL.filter(f => /styles\/ledger\.css|ledgerFonts/.test(readFileSync(f, 'utf-8')))
    expect(importers.length).toBeGreaterThan(0)
    for (const f of importers) {
      const rel = f.slice(ROOT.length + 1)
      expect(LEDGER_ROUTES.some(r => rel.startsWith(r)), `${rel} must not pull in the ledger theme`).toBe(true)
    }
  })

  it('inherits the seller palette while retaining scoped semantic aliases', () => {
    const css = read('styles', 'ledger.css')
    expect(css).not.toMatch(/--(?:bg|surface|surface-h|text|text-2|text-3|violet|success|danger|warning)\s*:/)
    expect(css).toContain('--ledger-green: var(--success)')
    expect(css).toContain('--ledger-oxide: var(--danger)')
  })
})

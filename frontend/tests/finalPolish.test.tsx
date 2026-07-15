import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// P6 — final design-system polish. Static guards that close the last drift the audit found:
// no old-violet literal on any seller/dashboard surface, the Account page is on P1 components,
// the card-hover motion is scoped (not transition:all), and the shared card classes that are
// still consumed are NOT removed.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')

const DASHBOARD_SURFACES = [
  ['app', 'dashboard', 'account', 'page.tsx'],
  ['app', 'dashboard', 'security', 'page.tsx'],
  ['app', 'dashboard', 'billing', 'page.tsx'],
  ['app', 'dashboard', 'page.tsx'],
  ['app', 'dashboard', 'reviews', 'page.tsx'],
  ['app', 'dashboard', 'import', 'page.tsx'],
  ['app', 'dashboard', 'settings', 'page.tsx'],
]

describe('P6 — old violet is gone from every seller/dashboard surface', () => {
  it.each(DASHBOARD_SURFACES)('%s carries no rgba(110,106,252) and no #6E6AFC', (...p) => {
    const src = read(...p)
    expect(src).not.toMatch(/110\s*,\s*106\s*,\s*252/)
    expect(src).not.toMatch(/6E6AFC/i)
  })

  it('the seller shell carries no old violet either', () => {
    const shell = read('components', 'seller', 'Shell.tsx')
    const css = read('styles', 'seller.css')
    for (const s of [shell, css]) {
      expect(s).not.toMatch(/110\s*,\s*106\s*,\s*252/)
      expect(s).not.toMatch(/6E6AFC/i)
    }
  })
})

describe('P6 — Account is on the P1 component system', () => {
  const acc = read('app', 'dashboard', 'account', 'page.tsx')

  it('imports and uses P1 Badge and Button', () => {
    expect(acc).toMatch(/@\/components\/ui\/badge/)
    expect(acc).toMatch(/@\/components\/ui\/button/)
    expect(acc).toMatch(/<Badge\b/)
    expect(acc).toMatch(/<Button\b/)
  })

  it('no longer uses the legacy .badge / .btn / .label utility classes', () => {
    expect(acc).not.toMatch(/className="[^"]*\bbadge\b/)
    expect(acc).not.toMatch(/className="[^"]*\bbtn\b/)
    expect(acc).not.toMatch(/className="[^"]*\blabel\b/)
  })
})

describe('P6 — card-hover motion is scoped, not transition:all', () => {
  const css = read('styles', 'seller.css')

  it('.s-clk and .s-pc use scoped token transitions, not the .14s all-shorthand', () => {
    const clk = css.match(/\.s-clk\{[^}]*\}/)?.[0] ?? ''
    const pc = css.match(/\.s-pc\{[^}]*\}/)?.[0] ?? ''
    for (const rule of [clk, pc]) {
      expect(rule).not.toMatch(/transition:\.14s/)
      expect(rule).toMatch(/transition:background-color var\(--dur\)/)
    }
  })
})

describe('P6 — shared card classes are retained (still consumed, not dead)', () => {
  it('.card / .stat-card / .card-bento remain defined because components use them', () => {
    const css = read('styles', 'globals.css')
    // these are consumed by CompetitorCard / MonitorEventCard / PriceHistory / ShareSuccessModal,
    // so they are NOT dead code and must stay defined
    expect(css).toMatch(/\.card\s*\{/)
    expect(css).toMatch(/\.stat-card\s*\{/)
    expect(css).toMatch(/\.card-bento\s*\{/)
    const consumers = ['CompetitorCard', 'MonitorEventCard', 'PriceHistory', 'ShareSuccessModal']
    for (const c of consumers) {
      expect(read('components', `${c}.tsx`)).toMatch(/className="[^"]*\b(card|stat-card|card-bento)\b/)
    }
  })
})

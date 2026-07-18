import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// Honest surfaces (Advisory MVP).
//
// The ONE marketplace-connection UI with a real backed purpose is the AR-CONTROL Auto Reviews
// panel (per-connection consent + enable/disable, enforced by the backend). Every OTHER use of
// /api/connections is still forbidden: nothing else lets a seller "connect a cabinet", nothing
// synchronises on its own, and no other action reaches a marketplace. Everything the product
// shows must be true under those facts.
//
// The execution surfaces (SellerAction's "⚡ Пульт сделает сам", OnboardingModal, the
// execution history) still exist as components but are mounted on NO page — P7.2 unmounted
// them deliberately. These tests hold that line: the day one of them is rendered again, or a
// page starts promising a marketplace sync, this fails instead of shipping a promise the
// product cannot keep.
//
// Delete these tests when a connections UI actually exists — not before.

const ROOT = join(__dirname, '..')

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

const APP_FILES = sourcesUnder(join(ROOT, 'app'))
const COMPONENT_FILES = sourcesUnder(join(ROOT, 'components'))
const RENDERED = [...APP_FILES, ...COMPONENT_FILES]

function read(f: string) {
  return readFileSync(f, 'utf-8')
}

/** Files that DEFINE a thing don't count — only files that USE it. */
function usages(name: string, definedIn: string): string[] {
  return RENDERED
    .filter((f) => !f.endsWith(definedIn))
    .filter((f) => new RegExp(`\\b${name}\\b`).test(read(f)))
}

describe('honest surfaces', () => {
  it('allows api.connections ONLY in the AR-CONTROL Auto Reviews panel', () => {
    // AR-CONTROL gives marketplace connections a real backed purpose:
    // seller-controlled Auto Reviews settings per connection.
    const ALLOW = join('components', 'reviews', 'AutoReviewsPanel.tsx')
    const callers = RENDERED.filter((f) => /api\/connections|api\.connections/.test(read(f)))
    // Any OTHER file touching api.connections is still a promise the product cannot keep — a bare
    // connections page, fake connection controls, or a new call site added without updating this
    // allow-list — and still fails. The allow-list is exactly one file, checked by path.
    const unexpected = callers.filter((f) => !f.endsWith(ALLOW))
    expect(unexpected).toEqual([])
    expect(callers.some((f) => f.endsWith(ALLOW))).toBe(true)
  })

  it('never promises a marketplace sync to a seller who has no data', () => {
    // The first-run screen once said "PULT подключён и ждёт данные с маркетплейса" and sent
    // the seller to a Telegram settings page. Nothing about that was true.
    const dashboard = read(join(ROOT, 'app', 'dashboard', 'page.tsx'))
    const rendered = dashboard.replace(/\/\*[\s\S]*?\*\//g, '')     // drop explanatory comments

    expect(rendered).not.toMatch(/PULT подключён/)
    expect(rendered).not.toMatch(/синхронизируются|синхронизации/)
    expect(rendered).not.toMatch(/Проверить подключение/)
    expect(rendered).toMatch(/\/dashboard\/import/)                 // the one road that exists
  })

  it('does not render the "Пульт сделает сам" execute bar (no cabinet can be connected)', () => {
    expect(usages('SellerAction', join('seller', 'Shell.tsx'))).toEqual([])
  })

  it('does not render the onboarding modal that teaches an unreachable flow', () => {
    expect(usages('OnboardingModal', join('cabinet', 'OnboardingModal.tsx'))).toEqual([])
  })

  it('does not show an execution history that can never contain a row', () => {
    expect(usages('ExecutionHistory', 'ExecutionHistory.tsx')).toEqual([])
    expect(usages('ActionHistory', join('cabinet', 'ActionHistory.tsx'))).toEqual([])
  })
})

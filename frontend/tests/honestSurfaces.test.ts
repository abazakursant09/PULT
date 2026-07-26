import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// Honest surfaces (Advisory MVP).
//
// The marketplace-connection UIs with a real backed purpose are: the AR-CONTROL Auto Reviews panel
// (per-connection consent + enable/disable, enforced by the backend) and — since CONNECTION-UI — the
// connections section itself, where a seller genuinely connects, re-checks, replaces and disconnects
// a cabinet against live backend endpoints. Every OTHER use of /api/connections stays forbidden.
//
// The guard is NOT retired now that a connections UI exists: its job has simply changed from "no
// such surface may exist" to "only these surfaces may exist". A second, decorative connections
// screen would be exactly the kind of promise this test was written to stop.
//
// The execution surfaces (SellerAction's "⚡ Пульт сделает сам", OnboardingModal, the
// execution history) still exist as components but are mounted on NO page — P7.2 unmounted
// them deliberately. These tests hold that line: the day one of them is rendered again, or a
// page starts promising a marketplace sync, this fails instead of shipping a promise the
// product cannot keep.

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
  it('allows api.connections ONLY in the surfaces that really connect a cabinet', () => {
    // Each entry earns its place by doing real, backend-enforced work:
    //   AutoReviewsPanel      — per-connection Auto Reviews consent + enable/disable
    //   ConnectionsSection    — read-only status + re-check / disconnect (no create path since 1.4.5I)
    //   ConnectApiDialog      — the Stores place a seller binds a key to a chosen cabinet (1.4.5D)
    //   YandexCampaignMapping — reads campaigns + links them to stores after verify (1.4.5G/I)
    // The old Settings ConnectMarketplaceDialog is gone (1.4.5I): it created a connection with no
    // marketplace_account_id — the second, account-less path the store flow replaced.
    const ALLOW = [
      join('components', 'reviews', 'AutoReviewsPanel.tsx'),
      join('components', 'connections', 'ConnectionsSection.tsx'),
      join('components', 'stores', 'ConnectApiDialog.tsx'),
      join('components', 'stores', 'YandexCampaignMapping.tsx'),
    ]
    const callers = RENDERED.filter((f) => /api\/connections|api\.connections/.test(read(f)))
    // Any OTHER file touching api.connections is still a promise the product cannot keep — a second
    // connections page, fake connection controls, or a new call site added without updating this
    // allow-list — and still fails.
    const unexpected = callers.filter((f) => !ALLOW.some((a) => f.endsWith(a)))
    expect(unexpected).toEqual([])
    // …and every allowed file must actually still be a caller, so a stale entry cannot quietly
    // widen the allow-list for whatever is added at that path next.
    for (const a of ALLOW) {
      expect(callers.some((f) => f.endsWith(a))).toBe(true)
    }
  })

  it('creates a connection ONLY through the single store flow (never account-less)', () => {
    // PULT-LAUNCH-1.4.5I: every new connection is bound to a chosen cabinet. Only ConnectApiDialog,
    // which requires a marketplace_account_id, may call connections.create — a guard against any file
    // resurrecting the old Settings path that minted an account-less connection.
    const CREATE_ALLOW = [join('components', 'stores', 'ConnectApiDialog.tsx')]
    const creators = RENDERED.filter((f) => /connections\.create\b/.test(read(f)))
    const unexpected = creators.filter((f) => !CREATE_ALLOW.some((a) => f.endsWith(a)))
    expect(unexpected).toEqual([])
    expect(creators.some((f) => f.endsWith(CREATE_ALLOW[0]))).toBe(true)
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

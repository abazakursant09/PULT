import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// LEGAL-1B guard — all frontend behavioral-event collection is removed.
//
// The old lib/events.ts client (trackEvent / getVisitorId / captureAttribution / stampFunnel)
// fired fire-and-forget POSTs to /api/events/track: anonymous visitor ids, UTM/referrer
// attribution, section-view and CTA-click telemetry. This test fails the day any of that surface
// reappears in production source — a call to trackEvent, an import of lib/events, or a literal
// /api/events endpoint.
//
// It scans production source only (app/, components/, lib/) — the tests dir and node_modules are
// excluded. Live UI/actions (executeInsight, navigation, form submit) are unaffected.

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

const FILES = [
  ...sourcesUnder(join(ROOT, 'app')),
  ...sourcesUnder(join(ROOT, 'components')),
  ...sourcesUnder(join(ROOT, 'lib')),
]

const read = (f: string) => readFileSync(f, 'utf-8')

const FORBIDDEN: { token: RegExp; label: string }[] = [
  { token: /\btrackEvent\b/, label: 'trackEvent call' },
  { token: /['"]@\/lib\/events['"]/, label: "import from '@/lib/events'" },
  { token: /\.\.?\/(?:[^'"]*\/)?lib\/events\b/, label: 'relative import of lib/events' },
  { token: /\/api\/events\b/, label: '/api/events endpoint' },
]

describe('LEGAL-1B: no behavioral-event collection in the frontend', () => {
  for (const { token, label } of FORBIDDEN) {
    it(`does not reference ${label} anywhere in app/components/lib`, () => {
      const offenders = FILES.filter((f) => token.test(read(f))).map((f) => f.slice(ROOT.length + 1))
      expect(offenders).toEqual([])
    })
  }
})

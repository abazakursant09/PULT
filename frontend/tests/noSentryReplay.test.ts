/**
 * LEGAL-1B (F) — structural guard: Sentry Session Replay (DOM/session recording) must never return.
 *
 * Ordinary Sentry error reporting stays (Sentry.init + dsn + tracesSampleRate). What is FORBIDDEN is the
 * Replay integration and its sample-rate knobs, which record the seller's DOM/session. This test reads the
 * client config as text and asserts none of those markers are present, and that error reporting remains.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const CLIENT_CONFIG = join(__dirname, '..', 'sentry.client.config.ts')

// Replay markers that must NOT appear as live code in the client config.
const FORBIDDEN = [
  'replayIntegration',
  'replaysSessionSampleRate',
  'replaysOnErrorSampleRate',
  'Replay(',
]

describe('LEGAL-1B Sentry Session Replay guard', () => {
  const src = readFileSync(CLIENT_CONFIG, 'utf8')

  for (const marker of FORBIDDEN) {
    it(`does not contain Session Replay marker "${marker}"`, () => {
      expect(src.includes(marker)).toBe(false)
    })
  }

  it('keeps ordinary Sentry error reporting (init + dsn + tracesSampleRate)', () => {
    expect(src).toContain('Sentry.init')
    expect(src).toContain('dsn:')
    expect(src).toContain('tracesSampleRate')
  })
})

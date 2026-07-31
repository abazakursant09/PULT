import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import * as session from '@/lib/session'

// SECURITY-2C-2 — the backend answers brute-force with 429 + a neutral Russian message and a Retry-After.
// The client contract for that response is narrow and load-bearing:
//   1. surface the neutral message verbatim (it must NOT reveal whether the email exists),
//   2. do NOT retry — retrying only deepens the block and delays the message,
//   3. do NOT clear the session like a 401 would (a throttled login is not an expired session).
// tests/setup.tsx installs a fetch guard forbidding real network; this file drives fetch itself and
// restores the guard afterwards.

const NEUTRAL = 'Слишком много попыток. Подождите и попробуйте снова.'

describe('api transport: a 429 throttle response is neutral, un-retried, and session-safe', () => {
  const networkGuard = globalThis.fetch
  afterEach(() => { globalThis.fetch = networkGuard; vi.restoreAllMocks() })

  it('login 429: shows the neutral message, calls fetch once, never clears the session', async () => {
    const clearSpy = vi.spyOn(session, 'clearSession')
    let calls = 0
    globalThis.fetch = ((): Promise<Response> => {
      calls++
      return Promise.resolve(new Response(JSON.stringify({ detail: NEUTRAL }), {
        status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '900' },
      }))
    }) as unknown as typeof fetch

    await expect(api.auth.login('victim@example.com', 'whatever')).rejects.toThrow(NEUTRAL)
    expect(calls).toBe(1)              // no client-side retry of a throttled request
    expect(clearSpy).not.toHaveBeenCalled()   // a 429 is not a 401 — the session stands
  })

  it('forgot-password 429: same neutral message, no retry (no email-existence oracle)', async () => {
    let calls = 0
    globalThis.fetch = ((): Promise<Response> => {
      calls++
      return Promise.resolve(new Response(JSON.stringify({ detail: NEUTRAL }), {
        status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '900' },
      }))
    }) as unknown as typeof fetch

    await expect(api.auth.forgotPassword('who@example.com')).rejects.toThrow(NEUTRAL)
    expect(calls).toBe(1)
  })

  it('puts the network guard back for everyone else', () => {
    expect(globalThis.fetch).toBe(networkGuard)
  })
})

import { afterEach, describe, expect, it } from 'vitest'

import { api } from '@/lib/api'

// Transport layer, not a screen. `req()` de-duplicates in-flight GETs through an `_inflight` map
// and used to clear that entry with `promise.finally(...)`. `.finally` returns a DERIVED promise
// which inherits the rejection of the original and which nobody handles — so every ordinary
// network failure raised an unhandled rejection even though the caller had caught the error
// properly. In the browser that is console noise and false alerts from error monitoring; in the
// test runner a single failed GET anywhere was enough to fail the whole run, which is what made
// the suite flake with nothing to attribute it to.
//
// `_inflight` is module-private, so cleanup is asserted the only way a caller can observe it:
// the next identical GET must actually go out again instead of being handed the rejected promise.

describe('api transport: a failed GET must not poison dedup or raise a stray rejection', () => {
  // tests/setup.tsx installs a guard as globalThis.fetch that forbids real network access. This
  // file is the one place that legitimately drives fetch itself, so it swaps the guard out and
  // must put it back — otherwise every later test in this file's worker would run unguarded.
  const networkGuard = globalThis.fetch
  afterEach(() => { globalThis.fetch = networkGuard })

  it('rejects the caller, clears the entry, and re-issues the next identical GET', async () => {
    const strayRejections: unknown[] = []
    const onUnhandled = (reason: unknown) => { strayRejections.push(reason) }
    // Installed for this test only, and removed in `finally` below — a listener left behind would
    // silently swallow real failures for the rest of the suite.
    process.on('unhandledRejection', onUnhandled)

    try {
      let fetchCalls = 0
      globalThis.fetch = (() => {
        fetchCalls++
        return Promise.reject(new Error('network down'))
      }) as unknown as typeof fetch

      // 1. the caller gets the ORIGINAL error, not a wrapped or swallowed one
      await expect(api.today.getSummary()).rejects.toThrow('network down')
      const afterFirst = fetchCalls
      expect(afterFirst).toBeGreaterThan(0)

      // 2. the identical GET runs again — proving `_inflight` was cleared and the rejected
      //    promise was NOT handed out a second time
      await expect(api.today.getSummary()).rejects.toThrow('network down')
      expect(fetchCalls).toBeGreaterThan(afterFirst)

      // 3. nothing extra was left unhandled. Give the microtask queue and one macrotask turn to
      //    settle first: unhandledRejection fires after the queue drains, so asserting
      //    immediately would pass even when a stray rejection is pending.
      await new Promise((resolve) => setTimeout(resolve, 50))
      expect(strayRejections).toEqual([])
    } finally {
      process.off('unhandledRejection', onUnhandled)
    }

    // The listener was per-test by contract — pin that it is really gone, or it would silently
    // absorb genuine failures for every test that follows.
    expect(process.listeners('unhandledRejection')).not.toContain(onUnhandled)
  })

  it('puts the network guard back for everyone else', () => {
    // If the swap above leaked, the rest of this worker would be free to hit the real network.
    // Asserted by identity on purpose: actually calling the guard would register a violation and
    // fail this test through the very afterEach check that makes the guard useful.
    expect(globalThis.fetch).toBe(networkGuard)
  })
})

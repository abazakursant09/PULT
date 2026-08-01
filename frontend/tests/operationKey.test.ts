import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { newOperationKey } from '@/lib/opkey'

// SECURITY-2D-1B-B — the client sends a canonical UUIDv4 as the `Idempotency-Key` header on manual
// executable writes, and NEVER sends an idempotency_key in the body (a body key is rejected 422 server
// side). The header is reused across transport retries because it lives in the request `init`.
// tests/setup.tsx installs a network guard; this file drives fetch itself and restores it afterwards.

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

function capture() {
  const seen: { url: string; init: RequestInit | undefined }[] = []
  globalThis.fetch = ((url: string, init?: RequestInit): Promise<Response> => {
    seen.push({ url: String(url), init })
    return Promise.resolve(new Response(JSON.stringify({ ok: true, status: 'success' }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
  }) as unknown as typeof fetch
  return seen
}

function header(init: RequestInit | undefined, name: string): string | undefined {
  const h = (init?.headers ?? {}) as Record<string, string>
  return h[name]
}

describe('opkey.newOperationKey', () => {
  it('mints a canonical lowercase UUIDv4', () => {
    const k = newOperationKey()
    expect(k).toMatch(V4)
    expect(k).toBe(k.toLowerCase())
  })
  it('mints a distinct key each call', () => {
    expect(newOperationKey()).not.toBe(newOperationKey())
  })
})

describe('api transport: operation-key header on manual executable writes', () => {
  const guard = globalThis.fetch
  afterEach(() => { globalThis.fetch = guard; vi.restoreAllMocks() })

  it('reviews.publish sends a valid Idempotency-Key header', async () => {
    const seen = capture()
    await api.reviews.publish('p1', 'r1', newOperationKey())
    expect(seen).toHaveLength(1)
    expect(header(seen[0].init, 'Idempotency-Key')).toMatch(V4)
  })

  it('executeInsight (real) sends the header; dry-run sends none', async () => {
    const seen = capture()
    await api.actionEngine.executeInsight('k', { dry_run: false, overrides: {} }, newOperationKey())
    await api.actionEngine.executeInsight('k', { dry_run: true, overrides: {} })
    expect(header(seen[0].init, 'Idempotency-Key')).toMatch(V4)
    expect(header(seen[1].init, 'Idempotency-Key')).toBeUndefined()
  })

  it('decisionApply.confirm never puts idempotency_key in the body', async () => {
    const seen = capture()
    await api.decisionApply.confirm('d1', { marketplace: 'wildberries', sku: 'S' })
    const body = JSON.parse(String(seen[0].init?.body))
    expect(body).not.toHaveProperty('idempotency_key')
  })

  it('puts the network guard back for everyone else', () => {
    expect(globalThis.fetch).toBe(guard)
  })
})

/**
 * SECURITY-2B-3 — the HTML CSP (Next) is hardened for production and permissive only in dev.
 */
import { describe, it, expect, vi } from 'vitest'
// next.config.js (CJS) exports buildCsp + securityHeaders for testing.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const nextConfig = require('../next.config.js')
const { buildCsp, securityHeaders } = nextConfig as {
  buildCsp: (isDev: boolean, apiOrigin?: string) => string
  securityHeaders: () => { key: string; value: string }[]
}
// A prod deploy sets NEXT_PUBLIC_API_URL to the app's own https origin (single-origin) — pass one here.
const PROD_API = 'https://app.example.com'

describe('SECURITY-2B-3 production CSP', () => {
  const prod = buildCsp(false, PROD_API)

  it('has no unsafe-eval', () => {
    expect(prod).not.toContain("'unsafe-eval'")
  })
  it('has no localhost', () => {
    expect(prod).not.toContain('localhost')
  })
  it('img-src has no bare https: (all images local)', () => {
    const img = prod.split(';').find(d => d.trim().startsWith('img-src'))!
    expect(img).toContain("'self'")
    expect(/\bhttps:(?!\/\/)/.test(img)).toBe(false)   // no bare `https:` source
    expect(img).not.toContain('https://')
  })
  it('connect-src has no bare https:', () => {
    const c = prod.split(';').find(d => d.trim().startsWith('connect-src'))!
    expect(/\bhttps:(?!\/\/)/.test(c)).toBe(false)
  })
  it('locks down object/base/frame/form/media', () => {
    expect(prod).toContain("object-src 'none'")
    expect(prod).toContain("base-uri 'none'")
    expect(prod).toContain("frame-src 'none'")
    expect(prod).toContain("frame-ancestors 'none'")
    expect(prod).toContain("form-action 'self'")
    expect(prod).toContain("media-src 'none'")
    expect(prod).toContain("manifest-src 'self'")
  })
  it('upgrades insecure requests in prod', () => {
    expect(prod).toContain('upgrade-insecure-requests')
  })
  it('KNOWN RESIDUAL: script-src still allows unsafe-inline (static-render nonce deferred)', () => {
    // documented residual — static generation of 55 pages precludes a per-request nonce for now
    expect(prod).toContain("script-src 'self' 'unsafe-inline'")
  })
})

describe('SECURITY-2B-3 development CSP (permissive only in dev)', () => {
  const dev = buildCsp(true)
  it('allows unsafe-eval (React Refresh) and localhost/ws (HMR)', () => {
    expect(dev).toContain("'unsafe-eval'")
    expect(dev).toContain('localhost')
    expect(dev).toContain('ws://')
  })
  it('does NOT upgrade-insecure-requests (breaks http://localhost)', () => {
    expect(dev).not.toContain('upgrade-insecure-requests')
  })
})

describe('SECURITY-2B-3 security headers', () => {
  it('includes COOP, CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy', () => {
    const keys = securityHeaders().map(h => h.key)
    for (const k of ['Cross-Origin-Opener-Policy', 'Content-Security-Policy', 'X-Frame-Options',
                     'X-Content-Type-Options', 'Referrer-Policy', 'Permissions-Policy']) {
      expect(keys).toContain(k)
    }
  })
  it('HSTS gated on non-dev (NODE_ENV)', () => {
    vi.stubEnv('NODE_ENV', 'test')
    expect(securityHeaders().map(h => h.key)).not.toContain('Strict-Transport-Security')
    vi.stubEnv('NODE_ENV', 'production')
    expect(securityHeaders().map(h => h.key)).toContain('Strict-Transport-Security')
    vi.unstubAllEnvs()
  })
})

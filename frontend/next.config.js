/** @type {import('next').NextConfig} */
// Backend origin the browser talks to. Baked at BUILD time from NEXT_PUBLIC_API_URL
// (Docker build arg in production) so a deployed frontend never depends on localhost.
// SECURITY: beta/production is single-origin (app serves /api via reverse proxy) → API_ORIGIN == self.
const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

// SECURITY-2B-3 — one CSP source for HTML routes, environment-aware.
//   prod: no 'unsafe-eval', no localhost, no bare https:; img/connect/font/worker are self + proven
//         values only; object-src/base-uri/frame-src/media-src locked down; form-action 'self';
//         upgrade-insecure-requests.
//   dev : adds 'unsafe-eval' (React Refresh), localhost API + HMR websocket.
// KNOWN RESIDUAL: script-src keeps 'unsafe-inline'. Next 15 App Router injects per-page inline
// bootstrap/hydration scripts; removing 'unsafe-inline' requires a per-REQUEST nonce, which forces
// every one of the 55 static pages into dynamic rendering. That trade-off is deferred to a
// pre-public-launch step (nonce + dynamic, or hash-based CSP). 'unsafe-eval' IS removed in prod.
function buildCsp(isDev, apiOrigin = API_ORIGIN) {
  const script = isDev
    ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
    : "script-src 'self' 'unsafe-inline'"
  // Single-origin prod (app serves /api) → apiOrigin == self, so this adds nothing extra; a cross-port
  // dev setup needs the explicit localhost API + HMR websocket.
  const connect = isDev
    ? `connect-src 'self' ${apiOrigin} http://localhost:8000 ws://localhost:3000 ws://127.0.0.1:3000`
    : `connect-src 'self' ${apiOrigin}`
  const directives = [
    "default-src 'self'",
    script,
    "style-src 'self' 'unsafe-inline'",     // Next/inline styles; style injection is not script execution
    "img-src 'self' data: blob:",           // all images are local (/public); next/image unused
    "font-src 'self' data:",                // next/font self-hosts Google fonts at build — no external host
    connect,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "manifest-src 'self'",
    "media-src 'none'",
  ]
  if (!isDev) directives.push('upgrade-insecure-requests')
  return directives.join('; ')
}

function securityHeaders() {
  const isDev = process.env.NODE_ENV !== 'production'
  const headers = [
    { key: 'X-Frame-Options', value: 'DENY' },
    { key: 'X-Content-Type-Options', value: 'nosniff' },
    { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
    { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=(), usb=()' },
    { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
    { key: 'Content-Security-Policy', value: buildCsp(isDev) },
  ]
  // HSTS only in non-dev (a real HTTPS deployment); never assert it over local http.
  if (!isDev) {
    headers.push({ key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' })
  }
  return headers
}

// Pages whose HTML can carry seller/account/finance data → never cached by a shared proxy or browser.
// Public static (/, /login shell, immutable /_next assets) are intentionally NOT no-store.
const SENSITIVE_PAGE_PREFIXES = ['/dashboard/:path*', '/dashboard', '/account/:path*', '/verify-email']

const nextConfig = {
  reactStrictMode: true,
  async headers() {
    const sec = securityHeaders()
    return [
      { source: '/(.*)', headers: sec },
      ...SENSITIVE_PAGE_PREFIXES.map(source => ({
        source,
        headers: [{ key: 'Cache-Control', value: 'no-store' }],
      })),
    ]
  },
}

module.exports = nextConfig
module.exports.buildCsp = buildCsp          // exported for tests
module.exports.securityHeaders = securityHeaders

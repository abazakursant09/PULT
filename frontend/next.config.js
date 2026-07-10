/** @type {import('next').NextConfig} */
// Backend origin the browser talks to. Baked at BUILD time from NEXT_PUBLIC_API_URL
// (Docker build arg in production) so a deployed frontend never depends on localhost.
const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Frame-Options',          value: 'DENY' },
          { key: 'X-Content-Type-Options',   value: 'nosniff' },
          { key: 'Referrer-Policy',          value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy',       value: 'camera=(), microphone=(), geolocation=()' },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: https: blob:",
              "font-src 'self' data:",
              `connect-src 'self' ${API_ORIGIN} https:`,
              "worker-src blob:",
              "frame-ancestors 'none'",
            ].join('; '),
          },
        ],
      },
    ]
  },
  // NOTE: the former "V2 canonical tabs" redirects were removed (P7.3). They
  // misrouted real Advisory-MVP surfaces (Monitor, Import) to now-hidden pages.
  // Every route now resolves to its own page (real, honest Coming-Soon, or 404).
}

module.exports = nextConfig

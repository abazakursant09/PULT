/** @type {import('next').NextConfig} */
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
              "connect-src 'self' http://localhost:8000 https:",
              "worker-src blob:",
              "frame-ancestors 'none'",
            ].join('; '),
          },
        ],
      },
    ]
  },
  // ПУЛЬТ V2 — старые роуты сворачиваются в 6 канонических вкладок.
  // Закладки/письма не ломаются: 307 redirect на новую структуру.
  async redirects() {
    return [
      { source: '/dashboard/leaks',           destination: '/dashboard/finance',  permanent: false },
      { source: '/dashboard/data',            destination: '/dashboard/products', permanent: false },
      { source: '/dashboard/monitor',         destination: '/dashboard/finance',  permanent: false },
      { source: '/dashboard/action-engine',   destination: '/dashboard',          permanent: false },
      { source: '/dashboard/seo',             destination: '/dashboard/products', permanent: false },
      { source: '/dashboard/seo-cards',       destination: '/dashboard/products', permanent: false },
      { source: '/dashboard/seo-intelligence',destination: '/dashboard/products', permanent: false },
      { source: '/dashboard/seo-lab',         destination: '/dashboard/products', permanent: false },
      { source: '/dashboard/notifications',   destination: '/dashboard/settings', permanent: false },
      { source: '/dashboard/import',          destination: '/dashboard/settings', permanent: false },
    ]
  },
}

module.exports = nextConfig

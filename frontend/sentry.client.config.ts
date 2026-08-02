// Install: npm i @sentry/nextjs
// Then add `withSentryConfig` wrapper in next.config.js

import * as Sentry from '@sentry/nextjs'

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NODE_ENV,
  enabled: process.env.NODE_ENV === 'production',
  tracesSampleRate: 0.1,
  // LEGAL-1B (F): Sentry Session Replay (DOM/session recording) intentionally removed. This config
  // captures errors only and never records the seller's session — no session-recording integration or
  // its sample-rate knobs. Do NOT reintroduce (guarded by tests/noSentryReplay.test.ts).
  ignoreErrors: [
    'ResizeObserver loop limit exceeded',
    'Non-Error promise rejection captured',
  ],
  beforeSend(event) {
    if (event.exception?.values?.[0]?.type === 'AbortError') return null
    return event
  },
})

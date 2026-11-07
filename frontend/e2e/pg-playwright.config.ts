import { defineConfig, devices } from '@playwright/test'
import path from 'path'

// Native disposable PostgreSQL is created by the confined runner, not a developer URL.
// The spec owns only its backend child so restart/restore tests exercise the same UI.
export default defineConfig({
  testDir: '.',
  testMatch: 'pg-acceptance.spec.ts',
  timeout: 600_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3097',
    actionTimeout: 20_000,
    navigationTimeout: 120_000,
    serviceWorkers: 'block',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    cwd: path.join(__dirname, '..'),
    command: 'npx --no-install next dev --port 3097 --hostname 127.0.0.1',
    port: 3097,
    reuseExistingServer: false,
    timeout: 180_000,
    env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8097', NEXT_TELEMETRY_DISABLED: '1' },
  },
})

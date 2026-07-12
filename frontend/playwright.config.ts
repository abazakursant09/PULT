import { defineConfig, devices } from '@playwright/test'
import { mkdirSync } from 'fs'
import path from 'path'

// One browser smoke for the Advisory MVP path (register → upload → diagnosis).
//
// Vitest already covers the components against mocked responses. This proves the parts a
// mock cannot: Next routing, a real session, the real frontend/backend contract, the real
// upload and the real diagnosis — rendered by a real browser.
//
// It runs against a THROWAWAY SQLite database and its own backend process. It never touches
// a developer's data, never needs marketplace credentials, and never talks to a marketplace.

// A fresh database file per run: no lock fights with a leftover process, and no chance of a
// previous run's rows explaining a pass.
//
// Playwright re-evaluates this config inside each worker process, so the name is minted once
// and then carried in the environment. Without that, the worker would invent a SECOND
// filename and read a database nobody ever wrote to.
const E2E_DB = process.env.E2E_DB_PATH
  ?? path.join(__dirname, 'e2e', '.tmp', `e2e-${Date.now()}.db`)
const API_PORT = 8099
const WEB_PORT = 3099
const API_URL = `http://127.0.0.1:${API_PORT}`

// The backend starts before any test hook runs, and SQLite will not create a missing
// directory for itself — so the throwaway database needs a home before uvicorn boots.
mkdirSync(path.dirname(E2E_DB), { recursive: true })

process.env.E2E_DB_PATH = E2E_DB          // the spec reads the verification token from here

export default defineConfig({
  testDir: './e2e',
  // The diagnosis only exists after the Advisory Runtime tick, which the scheduler fires on
  // a 60-second cadence. The test polls for it with a bounded wait rather than sleeping.
  timeout: 240_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,                              // one seller, one database
  retries: 0,
  reporter: [['list']],

  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },

  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  webServer: [
    {
      // Backend on a disposable SQLite file. APP_ENV=development keeps the production
      // fail-closed guards off; SMTP is deliberately unset, so send_email() logs the
      // verification link instead of delivering it and never raises (services/email.py).
      command: 'python -m uvicorn main:app --host 127.0.0.1 --port ' + API_PORT,
      cwd: path.join(__dirname, '..', 'backend'),
      port: API_PORT,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        APP_ENV: 'development',
        DATABASE_URL: `sqlite+aiosqlite:///${E2E_DB.replace(/\\/g, '/')}`,
        SECRET_KEY: 'e2e-only-secret-key-not-a-real-one',
        FRONTEND_URL: `http://127.0.0.1:${WEB_PORT}`,
        SMTP_HOST: '',
      },
    },
    {
      command: `npx next dev --port ${WEB_PORT} --hostname 127.0.0.1`,
      cwd: __dirname,
      port: WEB_PORT,
      reuseExistingServer: false,
      timeout: 180_000,
      env: { NEXT_PUBLIC_API_URL: API_URL },
    },
  ],
})

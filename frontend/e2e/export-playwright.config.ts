import { defineConfig, devices } from '@playwright/test'
import { mkdirSync } from 'fs'
import path from 'path'

const root = __dirname
const dir = path.join(root, '.tmp')
mkdirSync(dir, { recursive: true })
const db = path.join(dir, `export-browser-${Date.now()}.db`).replace(/\\/g, '/')
export default defineConfig({
  testDir: root, testMatch: 'export-download.pw.ts', workers: 1, retries: 0,
  timeout: 120_000, expect: { timeout: 30_000 },
  use: { baseURL: 'http://127.0.0.1:3104', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: 'python -m uvicorn --app-dir ../frontend/e2e export_backend:app --host 127.0.0.1 --port 8104',
      cwd: path.join(root, '../../backend'), url: 'http://127.0.0.1:8104/health', timeout: 120_000,
      reuseExistingServer: false, env: { APP_ENV: 'test', DATABASE_URL: `sqlite+aiosqlite:///${db}`,
        SECRET_KEY: 'synthetic-export-fixture-key-not-production', FRONTEND_URL: 'http://127.0.0.1:3104',
        SMTP_HOST: '', TELEGRAM_BOT_TOKEN: '', AUTO_MIGRATE: 'false' } },
    { command: 'node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3104',
      cwd: path.join(root, '..'), url: 'http://127.0.0.1:3104/login', timeout: 180_000,
      reuseExistingServer: false, env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8104', NEXT_TELEMETRY_DISABLED: '1' } },
  ],
})

import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

// Frontend test harness (MVP Path Assurance).
//
// The backend has 3494 tests proving things the seller never sees. Until now the ONE path
// a paying seller actually walks — register → upload a report → read the diagnosis — had
// none at all, so any regression in it shipped silently. These tests cover that path and
// nothing else: they render real components against MOCKED API responses, and never reach
// a backend.
export default defineConfig({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  plugins: [react() as any],   // vite version skew between Next and Vitest
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.tsx'],
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, '.') },   // mirrors tsconfig's "@/*" paths
  },
})

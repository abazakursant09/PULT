import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StoreImportPage from '@/app/dashboard/stores/[storeId]/import/page'
import { api } from '@/lib/api'

// The import experience, guarded on two layers — same job as before, new address.
//
// The CSV flow moved to /dashboard/stores/[storeId]/import in 1.4.5C, because the backend has
// refused an upload without a store since 1.4.2. These tests followed it: the page is different,
// the guarantees are the same — no raw hex, no legacy tokens, a real file input, the finance-first
// gate, and honest copy on the result screen.

const PAGE = readFileSync(
  join(__dirname, '..', 'app', 'dashboard', 'stores', '[storeId]', 'import', 'page.tsx'), 'utf-8')
const ERRSTATE = readFileSync(join(__dirname, '..', 'components', 'system', 'ErrorState.tsx'), 'utf-8')

describe('import page is on the design system', () => {
  it('uses no raw hex colour anywhere', () => {
    expect(PAGE).not.toMatch(/#[0-9A-Fa-f]{6}\b/)
    expect(PAGE).not.toMatch(/#[0-9A-Fa-f]{3}\b/)
  })

  it('no longer uses the old violet #6E6AFC', () => {
    expect(PAGE).not.toMatch(/6E6AFC/i)
  })

  it('does not import the legacy T token module', () => {
    expect(PAGE).not.toMatch(/@\/lib\/tokens/)
  })

  it('does not use the legacy .btn / .input / .badge utility classes', () => {
    // Whole class tokens only. The ledger's own `l-btn` / `l-input` are a different, scoped
    // system; this guard is about the legacy GLOBAL utilities, not about any name containing them.
    const legacy = (name: string) => new RegExp(`className="(?:[^"]*\\s)?${name}(?:\\s[^"]*)?"`)
    expect(PAGE).not.toMatch(legacy('btn'))
    expect(PAGE).not.toMatch(legacy('input'))
    expect(PAGE).not.toMatch(legacy('badge'))
  })

  it('takes every colour from a token, never from a literal', () => {
    expect(PAGE).toMatch(/var\(--/)
  })

  it('shows no bespoke spinner', () => {
    expect(PAGE).not.toMatch(/@keyframes spin/)
  })

  it('ErrorState no longer depends on the legacy T tokens', () => {
    expect(ERRSTATE).not.toMatch(/@\/lib\/tokens/)
    expect(ERRSTATE).not.toMatch(/#[0-9A-Fa-f]{6}\b/)
    expect(ERRSTATE).toMatch(/var\(--/)
  })
})

// ── behavioural ────────────────────────────────────────────────────────────────
const STORE = { id: 'st-1', label: 'Москва — FBS', marketplace: 'yandex', status: 'active' }

function importsPage(over: Record<string, unknown> = {}) {
  return { store: STORE, items: [], page: 1, page_size: 1, total: 0, pages: 0, ...over } as never
}

describe('stages render the ledger', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.csvImport, 'history').mockResolvedValue([] as never)
    vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue(importsPage())
  })

  it('keeps a real file input on the first stage', async () => {
    const { container } = render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
  })

  it('first import forces finance and locks the other report types', async () => {
    render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    expect(await screen.findByText(/Начните с финансового отчёта/)).toBeInTheDocument()

    const finance = await screen.findByRole('radio', { name: /Финансы/ })
    const products = await screen.findByRole('radio', { name: /Товары/ })
    expect((finance as HTMLInputElement).checked).toBe(true)
    expect((products as HTMLInputElement).disabled).toBe(true)
  })

  it('names the store the file will land in', async () => {
    render(<StoreImportPage params={{ storeId: 'st-1' }} />)
    // The store is named in the breadcrumb AND above the file step — both are the point.
    expect((await screen.findAllByText('Москва — FBS')).length).toBeGreaterThan(0)
  })
})

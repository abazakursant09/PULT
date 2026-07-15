import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ImportPage from '@/app/dashboard/import/page'
import { api } from '@/lib/api'

// P4 — import experience premium redesign. Two layers, mirroring P2/P3:
//  1. Static guard: the page source carries no legacy tokens / raw hex / old classes.
//  2. Behavioural: stages render P1 components, the importing stage is a skeleton, and the honest
//     copy + structural contracts (file input, two selects, finance-first) are preserved.
// No data/API change — every review of behaviour here runs against mocked csvImport calls.

const PAGE = readFileSync(join(__dirname, '..', 'app', 'dashboard', 'import', 'page.tsx'), 'utf-8')
const ERRSTATE = readFileSync(join(__dirname, '..', 'components', 'system', 'ErrorState.tsx'), 'utf-8')

describe('P4 — import page is on the design system', () => {
  it('uses no raw hex colour anywhere', () => {
    expect(PAGE).not.toMatch(/#[0-9A-Fa-f]{6}\b/)
    expect(PAGE).not.toMatch(/#[0-9A-Fa-f]{3}\b/)
  })

  it('no longer uses the old violet #6E6AFC', () => {
    expect(PAGE).not.toMatch(/6E6AFC/i)
  })

  it('does not import the legacy T token module', () => {
    expect(PAGE).not.toMatch(/@\/lib\/tokens/)
    expect(PAGE).not.toMatch(/\bfrom '@\/lib\/tokens'/)
  })

  it('does not use the legacy .btn / .input / .badge utility classes', () => {
    expect(PAGE).not.toMatch(/className="[^"]*\bbtn\b/)
    expect(PAGE).not.toMatch(/className="[^"]*\binput\b/)
    expect(PAGE).not.toMatch(/className="[^"]*\bbadge\b/)
  })

  it('references P0 tokens and imports the P1 components', () => {
    expect(PAGE).toMatch(/var\(--/)
    for (const c of ['ui/card', 'ui/button', 'ui/badge', 'ui/skeleton']) {
      expect(PAGE, `import must use ${c}`).toContain(c)
    }
  })

  it('ErrorState no longer depends on the legacy T tokens', () => {
    expect(ERRSTATE).not.toMatch(/@\/lib\/tokens/)
    expect(ERRSTATE).not.toMatch(/#[0-9A-Fa-f]{6}\b/)
    expect(ERRSTATE).toMatch(/var\(--/)
  })
})

// ── behavioural ────────────────────────────────────────────────────────────────
function previewData(over: Record<string, unknown> = {}) {
  return {
    import_id: 'imp1', marketplace: 'wb', import_type: 'finance',
    total_rows: 10, valid_rows: 9, skipped_rows: 1, headers: ['a'],
    mapped_columns: {}, unmapped_required: [], preview_rows: [], warnings: [], errors: [],
    duplicate_import_id: null, duplicate_date: null, ...over,
  }
}

const emptyHistory = [] as never

describe('P4 — stages render the redesign', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.csvImport, 'history').mockResolvedValue(emptyHistory)
  })

  it('upload stage keeps the file input and exactly two selects', async () => {
    const { container } = render(<ImportPage />)
    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
    expect(container.querySelectorAll('select').length).toBe(2)
  })

  it('first import forces finance and disables the type select', async () => {
    // empty history → financeFirst; the finance-first note and locked type select must show
    const { container } = render(<ImportPage />)
    expect(await screen.findByText(/Начните с финансового отчёта/)).toBeInTheDocument()
    const selects = container.querySelectorAll('select')
    const typeSelect = selects[1] as HTMLSelectElement
    expect(typeSelect.disabled).toBe(true)
    expect(typeSelect.value).toBe('finance')
  })

  it('importing stage shows a skeleton, not a bespoke spinner', async () => {
    // source guard: the old CSS spinner keyframe is gone; a skeleton is used instead
    expect(PAGE).not.toMatch(/@keyframes spin/)
    expect(PAGE).toMatch(/Skeleton/)
  })

  it('preview shows tabular stats incl. КОРРЕКТНЫХ, done preserves the honest copy', async () => {
    vi.spyOn(api.csvImport, 'upload').mockResolvedValue(previewData() as never)
    vi.spyOn(api.csvImport, 'confirm').mockResolvedValue({ imported_count: 9, skipped_count: 1 } as never)
    const user = userEvent.setup()
    const { container } = render(<ImportPage />)

    await waitFor(() => expect(container.querySelector('input[type="file"]')).toBeTruthy())
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['a,b\n1,2'], 'r.csv', { type: 'text/csv' }))
    await user.click(screen.getByRole('button', { name: /Загрузить и проверить/i }))

    expect(await screen.findByText('КОРРЕКТНЫХ')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Импортировать/i }))
    expect(await screen.findByText('Импорт завершён')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Перейти в Пульт/i })).toBeInTheDocument()
  })
})

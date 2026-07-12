import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Rail } from '@/components/seller/Shell'
import { PLAN_LABELS, planLabel, hasActiveSubscription } from '@/lib/plans'

// Release-honesty guards, slice 2. Each of these fails the day one of the fabrications
// returns to a surface the seller can reach.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')
const code = (...p: string[]) => read(...p).replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')

describe('seller navigation exposes no unreleasable surface', () => {
  it('does not lead to Тариф — the buy button reaches a real YooKassa charge', () => {
    // The tariff cards sell price monitoring, competitor analysis, AI review replies and a
    // "Финансовый модуль". The Advisory MVP delivers none of them, and the button below that
    // list creates a real payment. No seller-visible path may lead there until the commercial
    // contents are approved. The page and payments.py are untouched.
    render(<Rail />)

    expect(screen.queryByRole('link', { name: /Тариф/ })).not.toBeInTheDocument()
    expect(document.querySelector('a[href="/dashboard/billing"]')).toBeNull()
    expect(code('components', 'seller', 'Shell.tsx')).not.toMatch(/\/dashboard\/billing/)
  })

  it('does not lead to Идеи — that page reopens the legacy cabinet', () => {
    // /ideas renders AppShell, i.e. the old Sidebar: a green "МОНИТОРИНГ АКТИВЕН" beacon for
    // the contour removed for inventing news, plus four "Раздел в разработке" sections.
    render(<Rail />)

    expect(screen.queryByRole('link', { name: /Идеи/ })).not.toBeInTheDocument()
    expect(document.querySelector('a[href="/ideas"]')).toBeNull()
    expect(code('components', 'seller', 'Shell.tsx')).not.toMatch(/'\/ideas'/)
  })
})

describe('subscription status is never invented', () => {
  it('has no hardcoded active-status claim on Аккаунт', () => {
    const src = code('app', 'dashboard', 'account', 'page.tsx')

    expect(src).not.toMatch(/'СТАТУС'/)
    expect(src).not.toMatch(/'Активен'/)
  })

  it('gates the plan on a real payment, not on user.plan', () => {
    // user.plan defaults to 'master' at registration (backend models/user.py:18), so every
    // free account carried a paid-looking plan. subscription_end_date is the only unambiguous
    // field — the backend writes it when a payment activates the plan.
    expect(hasActiveSubscription(null)).toBe(false)
    expect(hasActiveSubscription({ subscription_end_date: null })).toBe(false)
    expect(hasActiveSubscription({ subscription_end_date: '2026-08-01' })).toBe(true)

    for (const page of ['account', 'billing']) {
      expect(code('app', 'dashboard', page, 'page.tsx')).toMatch(/hasActiveSubscription/)
    }
  })

  it('never calls an unknown plan paid or active', () => {
    expect(planLabel(undefined)).toBe('—')
    expect(planLabel('')).toBe('—')
    expect(planLabel('enterprise')).toBe('—')
    expect(planLabel('enterprise')).not.toMatch(/актив|беспл|плат/i)
  })
})

describe('billing carries no fabricated marketing claim', () => {
  it('has no ПОПУЛЯРНЫЙ badge — no popularity data exists', () => {
    const src = code('app', 'dashboard', 'billing', 'page.tsx')

    expect(src).not.toMatch(/ПОПУЛЯРНЫЙ/)
    expect(src).not.toMatch(/popular/)
  })

  it('has no unconditional АКТИВЕН badge', () => {
    expect(code('app', 'dashboard', 'billing', 'page.tsx')).not.toMatch(/АКТИВЕН/)
  })
})

describe('one plan value has exactly one name', () => {
  it('is named by a single canonical source, used by both pages', () => {
    // Billing said 'master' = "Старт"; Account said 'master' = "Мастер". The same seller read
    // two different tariffs on adjacent pages. Billing's names are canonical: they are the
    // names actually sold and the names printed on the payment history rows.
    expect(PLAN_LABELS).toEqual({ master: 'Старт', profi: 'Мастер', maximum: 'Профи' })

    for (const page of ['account', 'billing']) {
      const src = code('app', 'dashboard', page, 'page.tsx')
      expect(src).toMatch(/from '@\/lib\/plans'/)
      // no local map may shadow the canonical one
      expect(src).not.toMatch(/(PLAN_LABELS|planLabel)\s*(:|=)\s*\{/)
    }
  })
})

describe('the import page promises no section that does not exist', () => {
  it('does not say the data will appear in Финансы', () => {
    // There is no seller-visible "Финансы" section. What actually happens is that the runtime
    // picks the seller up on the next tick and the diagnosis lands on the dashboard.
    const src = read('app', 'dashboard', 'import', 'page.tsx')

    expect(src).not.toMatch(/в Финансах/)
    expect(src).toMatch(/Диагноз появится на главной|диагноз появится на главной/)
  })
})

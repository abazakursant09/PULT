import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// L0.1 — honest FREE Advisory-MVP launch. The payment surfaces must not sell unshipped modules,
// must not reach a charge, and must not simulate a successful payment. These guards fail the day
// any of those return. Static source guards + a behavioural render of the billing page.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')
// strip comments so the guards match live code, not explanatory prose
const code = (...p: string[]) => read(...p).replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')

const BILLING = ['app', 'dashboard', 'billing', 'page.tsx']
const CHECKOUT = ['app', 'checkout', 'page.tsx']
const RESULT = ['app', 'payment', 'result', 'page.tsx']

describe('L0.1 — billing sells nothing and reaches no charge', () => {
  const src = code(...BILLING)

  it('no buy button reaches api.payments.create', () => {
    expect(src).not.toMatch(/payments\.create/)
    expect(src).not.toMatch(/confirmation_url/)
  })

  it('no tariff cards advertising unshipped modules', () => {
    for (const module of ['Мониторинг цен', 'Анализ конкурентов', 'ИИ-ответы на отзывы', 'Финансовый модуль']) {
      expect(src, `billing must not advertise "${module}"`).not.toContain(module)
    }
    expect(src).not.toMatch(/Подключить/)
  })

  it('states the honest Advisory-MVP unavailable message', () => {
    expect(read(...BILLING)).toMatch(/Advisory MVP/)
    expect(read(...BILLING)).toMatch(/Платные тарифы будут доступны позже/)
  })
})

describe('L0.1 — billing renders the honest state, not a checkout', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear() })

  it('shows the unavailable notice and no "Подключить" button', async () => {
    localStorage.setItem('user', JSON.stringify({ id: 'u1', name: 'S', email: 's@e.com', plan: 'master' }))
    const { api } = await import('@/lib/api')
    vi.spyOn(api.payments, 'history').mockResolvedValue([] as never)
    const { default: BillingPage } = await import('@/app/dashboard/billing/page')

    render(<BillingPage />)
    expect(await screen.findByText(/Платные тарифы будут доступны позже/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Подключить/ })).not.toBeInTheDocument()
  })
})

describe('L0.1 — checkout cannot claim a fake success', () => {
  const src = code(...CHECKOUT)

  it('no setTimeout/localStorage payment simulation', () => {
    expect(src).not.toMatch(/setTimeout/)
    expect(src).not.toMatch(/localStorage\.setItem\(\s*['"]activePlan/)
  })

  it('never prints a successful-payment claim', () => {
    expect(read(...CHECKOUT)).not.toMatch(/Оплата прошла успешно/)
  })

  it('no unshipped-module tariff catalogue', () => {
    for (const module of ['Конкурентная разведка', 'Управление ценами', 'Автоответы на отзывы', 'Юридический модуль']) {
      expect(src, `checkout must not sell "${module}"`).not.toContain(module)
    }
  })

  it('shows the honest Advisory-MVP message', () => {
    expect(read(...CHECKOUT)).toMatch(/Платные тарифы будут доступны позже/)
  })
})

describe('L0.1 — payment result does not loop back into a charge', () => {
  it('cancelled/failed payment returns to the dashboard, not billing/checkout', () => {
    const src = code(...RESULT)
    expect(src).not.toMatch(/href="\/dashboard\/billing"/)
    expect(src).not.toMatch(/Попробовать снова/)
  })
})

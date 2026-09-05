import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Rail, SellerBar, NavProvider, ShellFrame } from '@/components/seller/Shell'

// P5 — responsive seller shell. Behavioural checks (nav items, active state, drawer open/close via
// hamburger + overlay + Escape) plus static guards on the shell CSS/TSX (violet active, scoped
// motion, ≥44px touch target). Presentation only — no routes, labels or hrefs change.

const nav = vi.hoisted(() => ({ path: '/dashboard/import' }))
vi.mock('next/navigation', () => ({
  usePathname: () => nav.path,
  useRouter: () => ({ push: vi.fn() }),
}))

const CSS = readFileSync(join(__dirname, '..', 'styles', 'seller.css'), 'utf-8')
const SHELL = readFileSync(join(__dirname, '..', 'components', 'seller', 'Shell.tsx'), 'utf-8')

function renderShell() {
  return render(
    <NavProvider>
      <ShellFrame>
        <Rail />
        <SellerBar title="Импорт данных" sub="sub" />
      </ShellFrame>
    </NavProvider>
  )
}

describe('P5 — navigation contents (unchanged)', () => {
  it('renders every MVP nav item with its href', () => {
    renderShell()
    const expected: [string, string][] = [
      ['Главная', '/dashboard'],
      // Stores joined the nav with 1.4.5C: it is a real, working page, and a CSV cannot be
      // uploaded without choosing one of them first.
      ['Магазины', '/dashboard/stores'],
      ['Импорт данных', '/dashboard/import'],
      ['Отзывы', '/dashboard/reviews'],
      ['Настройки', '/dashboard/settings'],
      ['Аккаунт', '/dashboard/account'],
    ]
    for (const [label, href] of expected) {
      const link = screen.getByRole('link', { name: new RegExp(label) })
      expect(link.getAttribute('href')).toBe(href)
    }
  })

  it('does not offer a page that is still a stub', () => {
    renderShell()
    // The unreleased global products route is fail-closed, and Decisions has no standalone page.
    // A nav item pointing at either would be a promise the product cannot keep.
    expect(screen.queryByRole('link', { name: /^Товары/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Решения/ })).toBeNull()
  })

  it('marks the current route active', () => {
    nav.path = '/dashboard/import'
    const { container } = renderShell()
    const active = container.querySelector('.s-nav.on')
    expect(active?.getAttribute('href')).toBe('/dashboard/import')
  })
})

// PULT-LAUNCH-1.4.5I — the active accent is GREEN only on the Executive Ledger store routes; every
// other section keeps the default accent.
describe('1.4.5I — scoped green nav on store routes', () => {
  it('a NON-store active route does not get the ledger green modifier', () => {
    nav.path = '/dashboard/import'
    const { container } = renderShell()
    const active = container.querySelector('.s-nav.on')
    expect(active?.getAttribute('href')).toBe('/dashboard/import')
    expect(active?.className).not.toContain('s-nav--ledger')
  })

  it('the active store route carries the ledger green modifier', () => {
    nav.path = '/dashboard/stores/abc'
    const { container } = renderShell()
    const active = container.querySelector('.s-nav.on')
    expect(active?.getAttribute('href')).toBe('/dashboard/stores')
    expect(active?.className).toContain('s-nav--ledger')
    nav.path = '/dashboard/import'   // restore for other tests
  })

  it('the green accent is scoped to s-nav--ledger and the default stays violet', () => {
    // default active stays the P0 violet …
    expect(CSS.match(/\.s-nav\.on\{[^}]*\}/)?.[0] ?? '').toMatch(/var\(--violet/)
    // … and the store-route override is a distinct green rule, never repainting other sections.
    const green = CSS.match(/\.s-nav\.on\.s-nav--ledger\{[^}]*\}/)?.[0] ?? ''
    expect(green).toMatch(/46,94,78|2E5E4E/)
    expect(CSS).toMatch(/\.s-nav\.on\.s-nav--ledger \.s-nav-ic\{[^}]*(6FBF9B|2E5E4E)/)
  })
})

describe('P5 — drawer open / close', () => {
  it('hamburger exists and toggles the drawer open', () => {
    const { container } = renderShell()
    expect(container.querySelector('.s-app')?.className).not.toContain('nav-open')
    fireEvent.click(screen.getByRole('button', { name: /Открыть меню/ }))
    expect(container.querySelector('.s-app')?.className).toContain('nav-open')
  })

  it('overlay click closes the drawer', () => {
    const { container } = renderShell()
    fireEvent.click(screen.getByRole('button', { name: /Открыть меню/ }))
    expect(container.querySelector('.s-app')?.className).toContain('nav-open')
    fireEvent.click(container.querySelector('.s-scrim')!)
    expect(container.querySelector('.s-app')?.className).not.toContain('nav-open')
  })

  it('navigation click closes the drawer', () => {
    const { container } = renderShell()
    fireEvent.click(screen.getByRole('button', { name: /Открыть меню/ }))
    fireEvent.click(screen.getByRole('link', { name: /Отзывы/ }))
    expect(container.querySelector('.s-app')?.className).not.toContain('nav-open')
  })

  it('Escape closes the drawer', () => {
    const { container } = renderShell()
    fireEvent.click(screen.getByRole('button', { name: /Открыть меню/ }))
    expect(container.querySelector('.s-app')?.className).toContain('nav-open')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(container.querySelector('.s-app')?.className).not.toContain('nav-open')
  })

  it('Escape closes the nested theme picker before the drawer', () => {
    const { container } = renderShell()
    fireEvent.click(screen.getByRole('button', { name: /Открыть меню/ }))
    fireEvent.click(screen.getByRole('button', { name: /Тема:/ }))

    const themes = screen.getByRole('radiogroup', { name: 'Тема оформления' })
    fireEvent.keyDown(themes, { key: 'Escape' })

    expect(screen.queryByRole('dialog', { name: 'Выбор рабочей темы' })).toBeNull()
    expect(container.querySelector('.s-app')?.className).toContain('nav-open')
  })
})

describe('P5 — SellerBar API preserved', () => {
  it('renders title, sub and a right slot', () => {
    render(
      <NavProvider>
        <SellerBar title="Заголовок" sub="Подзаголовок" right={<span>ПРАВО</span>} />
      </NavProvider>
    )
    expect(screen.getByText('Заголовок')).toBeInTheDocument()
    expect(screen.getByText('Подзаголовок')).toBeInTheDocument()
    expect(screen.getByText('ПРАВО')).toBeInTheDocument()
  })
})

describe('P5 — static design-system guards', () => {
  // active nav accent is the P0 violet, never the metallic --ac
  it('active nav uses --violet, not --ac', () => {
    const onRule = CSS.match(/\.s-nav\.on\{[^}]*\}/)?.[0] ?? ''
    expect(onRule).toMatch(/var\(--violet/)
    const onIcon = CSS.match(/\.s-nav\.on \.s-nav-ic\{[^}]*\}/)?.[0] ?? ''
    expect(onIcon).toMatch(/var\(--violet/)
    expect(onIcon).not.toMatch(/var\(--ac\)/)
  })

  it('the nav/rail/drawer transitions are scoped, not transition:all / bare .12s', () => {
    // the rewritten shell classes must not carry the old all-properties shorthand
    const navRule = CSS.match(/\.s-nav\{[^}]*\}/)?.[0] ?? ''
    expect(navRule).toMatch(/transition:background-color var\(--dur\)/)
    expect(navRule).not.toMatch(/transition:\.\d/)
    expect(CSS).not.toMatch(/\.s-rail\{[^}]*transition:\.\d/)
    // drawer slide uses a token curve
    expect(CSS).toMatch(/transform var\(--dur\) var\(--ease-drawer\)/)
  })

  it('mobile nav touch targets are at least 44px', () => {
    const mobile = CSS.match(/@media \(max-width:1024px\)\{[\s\S]*?\n\}/)?.[0] ?? ''
    expect(mobile).toMatch(/\.s-nav\{min-height:44px/)
  })

  it('off-canvas primitives exist and the drawer curve token is defined', () => {
    expect(SHELL).toMatch(/NavProvider/)
    expect(SHELL).toMatch(/ShellFrame/)
    expect(SHELL).toMatch(/s-burger/)
    expect(SHELL).toMatch(/s-scrim/)
    const globals = readFileSync(join(__dirname, '..', 'styles', 'globals.css'), 'utf-8')
    expect(globals).toMatch(/--ease-drawer:/)
  })

  it('legacy dead code is gone (SellerAction, .s-search, .s-daypill)', () => {
    expect(SHELL).not.toMatch(/SellerAction/)
    expect(CSS).not.toMatch(/\.s-search\b/)
    expect(CSS).not.toMatch(/\.s-daypill\b/)
  })
})

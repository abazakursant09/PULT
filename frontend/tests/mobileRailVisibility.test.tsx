import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NavProvider, Rail, SellerBar } from '@/components/seller/Shell'
import { ThemeProvider } from '@/components/theme/ThemeProvider'

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push: vi.fn() }),
}))

const QUERY = '(max-width:1024px)'
let mobile = true
let listeners: Set<() => void>
let add: ReturnType<typeof vi.fn>
let remove: ReturnType<typeof vi.fn>

beforeEach(() => {
  mobile = true
  listeners = new Set()
  add = vi.fn((_event: string, listener: () => void) => listeners.add(listener))
  remove = vi.fn((_event: string, listener: () => void) => listeners.delete(listener))
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    media: query,
    get matches() { return query === QUERY && mobile },
    addEventListener: query === QUERY ? add : vi.fn(),
    removeEventListener: query === QUERY ? remove : vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), onchange: null,
    dispatchEvent: vi.fn(),
  })))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function mount() {
  const view = render(<ThemeProvider><NavProvider><Rail /><SellerBar title="Test" /></NavProvider></ThemeProvider>)
  return { ...view, rail: view.container.querySelector('aside')! }
}

function resize(value: boolean) {
  act(() => { mobile = value; listeners.forEach(listener => listener()) })
}

function expectHidden(rail: HTMLElement, hidden: boolean) {
  // jsdom attributes prove the browser contract, not native Tab suppression.
  if (hidden) {
    expect(rail).toHaveAttribute('inert', '')
    expect(rail).toHaveAttribute('aria-hidden', 'true')
  } else {
    expect(rail).not.toHaveAttribute('inert')
    expect(rail).not.toHaveAttribute('aria-hidden')
  }
}

describe('mobile Rail visibility', () => {
  it('preserves navigation without crashing when matchMedia is unavailable', () => {
    vi.stubGlobal('matchMedia', undefined)
    expectHidden(mount().rail, false)
  })
  it('makes the closed mobile rail inert and hidden from AT', () => {
    expectHidden(mount().rail, true)
    expect(window.matchMedia).toHaveBeenCalledWith(QUERY)
  })

  it('removes attributes when opened and restores them when closed', () => {
    const { rail } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть меню' }))
    expectHidden(rail, false)
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть меню' }))
    expectHidden(rail, true)
  })

  it('keeps desktop accessible with either drawer state', () => {
    mobile = false
    const { rail } = mount()
    expectHidden(rail, false)
    fireEvent.click(screen.getByRole('button', { name: 'Открыть меню' }))
    expectHidden(rail, false)
  })

  it('updates a closed rail across viewport changes', () => {
    const { rail } = mount()
    resize(false)
    expectHidden(rail, false)
    resize(true)
    expectHidden(rail, true)
  })

  it('keeps an open rail accessible across viewport changes', () => {
    const { rail } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть меню' }))
    resize(false)
    expectHidden(rail, false)
    resize(true)
    expectHidden(rail, false)
  })

  it('replaces the listener on open updates and removes it on unmount', () => {
    const { unmount } = mount()
    expect(listeners.size).toBe(1)
    const first = add.mock.calls[0][1]
    fireEvent.click(screen.getByRole('button', { name: 'Открыть меню' }))
    expect(remove).toHaveBeenCalledWith('change', first)
    expect(listeners.size).toBe(1)
    const current = add.mock.calls.at(-1)![1]
    unmount()
    expect(remove).toHaveBeenCalledWith('change', current)
    expect(listeners.size).toBe(0)
  })
})

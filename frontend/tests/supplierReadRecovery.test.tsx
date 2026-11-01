import { Component, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'fs'
import { join } from 'path'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SupplierVerifyPage from '@/app/dashboard/suppliers/verify/page'
import { api } from '@/lib/api'

// The old feature/read-recovery acceptance is superseded by intentional quarantine.
const { sentinel, notFound } = vi.hoisted(() => {
  const sentinel = new Error('SYNTHETIC_NEXT_NOT_FOUND')
  return { sentinel, notFound: vi.fn((): never => { throw sentinel }) }
})
vi.mock('next/navigation', () => ({ notFound }))

class NotFoundBoundary extends Component<{ children: ReactNode }, { stopped: boolean }> {
  state = { stopped: false }
  static getDerivedStateFromError(error: unknown) {
    if (error !== sentinel) throw error
    return { stopped: true }
  }
  render() { return this.state.stopped ? null : this.props.children }
}

beforeEach(() => {
  notFound.mockClear()
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('No network allowed'))
  for (const method of Object.keys(api.suppliers) as Array<keyof typeof api.suppliers>) {
    vi.spyOn(api.suppliers, method).mockResolvedValue([] as never)
  }
})
afterEach(() => vi.restoreAllMocks())
function expectNoRequests() {
  expect(globalThis.fetch).not.toHaveBeenCalled()
  for (const method of Object.values(api.suppliers)) expect(method).not.toHaveBeenCalled()
}

it('immediately invokes Next notFound before any supplier API or fetch', () => {
  // Direct invocation proves the synchronous server-page exit, not a client redirect.
  expect(() => SupplierVerifyPage()).toThrow(sentinel)
  expect(notFound).toHaveBeenCalledTimes(1)
  expectNoRequests()
})

it('renders no form, registry, badges or controls before the notFound boundary', () => {
  // React deliberately reports an error caught by a boundary; silence only this sentinel.
  const expectedError = (event: ErrorEvent) => { if (event.error === sentinel) event.preventDefault() }
  const originalError = console.error
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    if (args.some(arg => arg === sentinel || (typeof arg === 'string' && arg.includes('The above error occurred in the <SupplierVerifyPage>')))) return
    originalError(...args)
  })
  window.addEventListener('error', expectedError)
  try {
    const { container } = render(<NotFoundBoundary><SupplierVerifyPage /></NotFoundBoundary>)
    expect(notFound).toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
    expect(container.querySelector('form, input, select, button, svg')).toBeNull()
    expect(screen.queryByText(/Проверенный производитель|Верифицирован|Подать заявку/)).not.toBeInTheDocument()
    expectNoRequests()
  } finally {
    window.removeEventListener('error', expectedError)
  }
})

it('keeps quarantine server-only with no API imports, effects or feature UI', () => {
  const source = readFileSync(join(__dirname, '../app/dashboard/suppliers/verify/page.tsx'), 'utf8')
  expect(source).toContain("from 'next/navigation'")
  expect(source).not.toMatch(/use client|@\/lib\/api|useEffect|useState|<form|<Badge|<button/)
})

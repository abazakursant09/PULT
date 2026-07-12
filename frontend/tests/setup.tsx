import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Next's router is not available outside the app runtime; components under test call it on
// mount (the dashboard redirects to /login when there is no token).
export const routerPush = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: routerPush, refresh: vi.fn() }),
  usePathname: () => '/dashboard',
  useSearchParams: () => new URLSearchParams(),
}))

// next/link renders a plain anchor so hrefs stay assertable.
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: any) =>
    <a href={typeof href === 'string' ? href : String(href)} {...rest}>{children}</a>,
}))

afterEach(() => {
  cleanup()
  routerPush.mockReset()
  localStorage.clear()
})

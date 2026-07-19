import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Next's router is not available outside the app runtime; components under test call it on
// mount (the dashboard redirects to /login when there is no token).
export const routerPush = vi.fn()
export const routerRefresh = vi.fn()

vi.mock('next/navigation', () => {
  // ONE router object for the whole module, not a fresh one per useRouter() call.
  //
  // The real next/navigation returns a stable reference, and components rely on that. Handing
  // back a new object each call breaks any `useCallback(..., [router])`, which yields a new
  // callback identity on every render, which re-fires the `useEffect(..., [callback])` that
  // depends on it — an unbounded render→effect→setState→render loop. The billing page is built
  // exactly that way, and it spun ~1000 iterations/second: the worker died of heap exhaustion,
  // and once the test's own API spy was restored the still-running loop hit the real network.
  // The bug was never in the page or in that test — it was here, in the shared harness.
  //
  // `refresh` is created once for the same reason: a `vi.fn()` per call also accumulated
  // forever in the mock registry.
  const router = { push: routerPush, replace: routerPush, refresh: routerRefresh }
  return {
    useRouter: () => router,
    usePathname: () => '/dashboard',
    useSearchParams: () => new URLSearchParams(),
  }
})

// next/link renders a plain anchor so hrefs stay assertable.
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: any) =>
    <a href={typeof href === 'string' ? href : String(href)} {...rest}>{children}</a>,
}))

afterEach(() => {
  cleanup()
  routerPush.mockReset()
  routerRefresh.mockReset()   // reset in place: the identity must survive, only the calls reset
  localStorage.clear()
})

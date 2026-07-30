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
    // Dynamic-route pages read their segment via useParams() (Next 15 client-component pattern,
    // React-18 compatible). Tests render them with fixed ids (st-1 / imp-1); one stable object
    // covers every dynamic page under test — each picks the key it needs.
    useParams: () => ({ storeId: 'st-1', importId: 'imp-1' }),
  }
})

// Telemetry is infrastructure, never the thing under test — and it is the one module that fires
// on a TIMER. `trackEvent` defers through requestIdleCallback/setTimeout and then POSTs
// /api/events/track, so a component that tracks anything schedules a real request that lands
// AFTER the test that caused it has finished. By then the environment is being torn down and the
// rejection has no live handler left: an unhandled rejection, attributed to no test, failing the
// run about half the time. Stubbed here so no test can schedule one.
//
// Two test files mocked '@/lib/analytics' for this. That module does not exist — the real one is
// '@/lib/events' — so those mocks silently did nothing. A mock of a path that does not resolve is
// indistinguishable from working, which is why this went unnoticed.
vi.mock('@/lib/events', () => ({
  trackEvent: vi.fn(),
  stampFunnel: vi.fn(),
  elapsedSince: () => undefined,
  firstTimeOnly: () => false,
  getVisitorId: () => 'test-visitor',
  captureAttribution: vi.fn(),
  FUNNEL_TS: { signup: 'bp_ts_signup', firstImport: 'bp_ts_first_import', firstInsight: 'bp_ts_first_insight' },
}))

// No test may reach the network. Two things are needed, and throwing alone is only the first:
//
//  1. Throw, so nothing is actually sent and no fake data hides the omission. Returning a stubbed
//     response would conceal unmocked calls — which is how the import page came to be fetching
//     /api/import/history for real in a unit test.
//  2. RECORD the URL and fail the offending test in afterEach. Throwing on its own is not enough,
//     because the components here fail open: they catch the error, render their fallback, and the
//     assertions still pass. The test goes green while a call it never mocked escapes into
//     lib/api.ts's retry chain and surfaces later, attributed to nobody.
//
// So the violation is reported against the exact test that caused it, synchronously, by URL.
const unexpectedRequests: string[] = []

globalThis.fetch = ((...args: unknown[]) => {
  const url = String(args[0])
  unexpectedRequests.push(url)
  throw new Error(
    `Unmocked network call in a test: ${url}\n` +
    'Mock the api client method the component calls (vi.spyOn(api.<group>, "<method>")).',
  )
}) as unknown as typeof fetch

// next/link renders a plain anchor so hrefs stay assertable.
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: any) =>
    <a href={typeof href === 'string' ? href : String(href)} {...rest}>{children}</a>,
}))

afterEach(() => {
  cleanup()                   // unmount first, so anything fired during teardown is counted too
  routerPush.mockReset()
  routerRefresh.mockReset()   // reset in place: the identity must survive, only the calls reset
  localStorage.clear()

  // Fail the test that made the call, not a later innocent one. Reset before throwing so a single
  // offender cannot cascade into every test that follows it.
  if (unexpectedRequests.length) {
    const urls = [...new Set(unexpectedRequests)]
    unexpectedRequests.length = 0
    throw new Error(
      `This test made ${urls.length} unmocked network call(s):\n  ${urls.join('\n  ')}\n` +
      'Stub the api client method the component calls on mount.',
    )
  }
})

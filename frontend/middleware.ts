import { NextRequest, NextResponse } from 'next/server'
import { FLAGS } from '@/lib/featureFlags'
import {
  AUTHENTICATED_TOOL_PREFIXES,
  isRouteUnavailable,
  matchesRoutePrefix,
} from '@/lib/routePolicy'

/**
 * Edge-level UX guard (SECURITY-2B-2).
 * Checks only for the PRESENCE of the backend-set HttpOnly session cookie to avoid an unauthenticated
 * SSR flicker. It is NOT a security boundary: it never decodes the JWT, and a forged cookie only opens
 * a frontend route — every /api request is still re-validated by the backend, which returns 401. The
 * cookie name differs by environment (dev vs the `__Host-` prefixed prod name), so both are accepted.
 */

const PROTECTED_PREFIXES = ['/dashboard', '/checkout', ...AUTHENTICATED_TOOL_PREFIXES]
const COOKIE_NAMES = ['__Host-pult_session', 'pult_session_dev']

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl

  // Product-release boundary, not an authorization redirect. Return a neutral 404 before looking
  // at cookies so placeholders and disabled commercial/growth surfaces cannot be enumerated.
  if (isRouteUnavailable(pathname, FLAGS)) {
    return new NextResponse(null, { status: 404 })
  }

  const isProtected = PROTECTED_PREFIXES.some(prefix => matchesRoutePrefix(pathname, prefix))

  if (!isProtected) return NextResponse.next()

  const token = COOKIE_NAMES.map(n => request.cookies.get(n)?.value).find(Boolean)
  if (!token) {
    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('from', pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    // Run on all paths except Next.js internals and static files
    '/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|woff2?|ttf|eot)$).*)',
  ],
}

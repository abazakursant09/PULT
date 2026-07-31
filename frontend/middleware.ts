import { NextRequest, NextResponse } from 'next/server'

/**
 * Edge-level UX guard (SECURITY-2B-2).
 * Checks only for the PRESENCE of the backend-set HttpOnly session cookie to avoid an unauthenticated
 * SSR flicker. It is NOT a security boundary: it never decodes the JWT, and a forged cookie only opens
 * a frontend route — every /api request is still re-validated by the backend, which returns 401. The
 * cookie name differs by environment (dev vs the `__Host-` prefixed prod name), so both are accepted.
 */

const PROTECTED_PREFIXES = ['/dashboard', '/checkout']
const COOKIE_NAMES = ['__Host-pult_session', 'pult_session_dev']

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl

  const isProtected = PROTECTED_PREFIXES.some(
    prefix => pathname === prefix || pathname.startsWith(`${prefix}/`),
  )

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

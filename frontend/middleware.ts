import { NextRequest, NextResponse } from 'next/server'

/**
 * Edge-level auth guard.
 * Reads pult_token cookie (set by lib/session.ts on login).
 * Does NOT validate the JWT — that stays on the backend API.
 * Presence check is enough to block unauthenticated SSR flicker.
 */

const PROTECTED_PREFIXES = ['/dashboard', '/checkout']
const COOKIE_NAME = 'pult_token'

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl

  const isProtected = PROTECTED_PREFIXES.some(
    prefix => pathname === prefix || pathname.startsWith(`${prefix}/`),
  )

  if (!isProtected) return NextResponse.next()

  const token = request.cookies.get(COOKIE_NAME)?.value
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

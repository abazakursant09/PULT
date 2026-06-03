/**
 * Centralised auth session helper.
 * All token reads/writes go through here — never directly touch localStorage or cookies elsewhere.
 *
 * Cookie `pult_token`:
 *   - JS-readable (not HttpOnly) so the login page can set it client-side.
 *   - Read by Next.js middleware at the edge — no round-trip to the backend needed.
 *   - Architecture is designed for a future migration to HttpOnly + refresh tokens:
 *     just move setToken to a server action / API route and add HttpOnly flag there.
 */

const TOKEN_KEY   = 'token'
const USER_KEY    = 'user'
const COOKIE_NAME = 'pult_token'
const MAX_AGE     = 60 * 60 * 24 * 60 // 60 days

function isHttps(): boolean {
  return typeof window !== 'undefined' && window.location.protocol === 'https:'
}

/**
 * Returns the auth token.
 * Priority: cookie (edge-readable) → localStorage (legacy fallback).
 */
export function getToken(): string | null {
  if (typeof window === 'undefined') return null
  const match = document.cookie
    .split('; ')
    .find(row => row.startsWith(`${COOKIE_NAME}=`))
  if (match) {
    const val = match.split('=').slice(1).join('=')
    const decoded = decodeURIComponent(val)
    if (decoded) return decoded
  }
  return localStorage.getItem(TOKEN_KEY)
}

/**
 * Persists token + user to both cookie and localStorage.
 * Called after every successful login / email-verify / OAuth flow.
 */
export function setToken(accessToken: string, user: unknown): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(TOKEN_KEY, accessToken)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  const secure = isHttps() ? '; Secure' : ''
  document.cookie = [
    `${COOKIE_NAME}=${encodeURIComponent(accessToken)}`,
    'path=/',
    'SameSite=Lax',
    `Max-Age=${MAX_AGE}`,
    secure,
  ].filter(Boolean).join('; ')
}

/**
 * Clears all session state: localStorage + cookie.
 * Call this on logout or when backend returns 401.
 */
export function clearSession(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  // Expire the cookie immediately
  document.cookie = `${COOKIE_NAME}=; path=/; SameSite=Lax; Max-Age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT`
}

/** Returns the stored user object, or null if not found / invalid JSON. */
export function getUser<T = unknown>(): T | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

/** True if a token exists in cookie or localStorage. Does NOT validate JWT. */
export function isAuthenticated(): boolean {
  return Boolean(getToken())
}

/**
 * SECURITY-2B-2 — the auth session lives ONLY in a backend-set Secure HttpOnly cookie.
 *
 * JavaScript can no longer read or write the session token: there is no getToken/setToken, nothing is
 * kept in localStorage or a JS-readable cookie, and every API call authenticates via `credentials:
 * 'include'` (the browser attaches the HttpOnly cookie automatically). This module keeps only the
 * NON-SECRET user profile for instant UI hydration, plus a one-time purge of any pre-2B-2 token/cookie.
 */

const USER_KEY = 'user'
// Legacy keys from the old Bearer/localStorage scheme — PURGED once, never read or sent.
const LEGACY_TOKEN_KEY = 'token'
const LEGACY_COOKIE = 'pult_token'

/** Persist the non-secret user profile (returned by login/verify/MFA). Never a token. */
export function setUser(user: unknown): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  purgeLegacyAuth()
}

/** The stored user profile, or null. UI hint only — the real auth boundary is the cookie + backend. */
export function getUser<T = unknown>(): T | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

/** Drop local UI state on logout / 401. Does NOT touch the HttpOnly cookie (only the backend can). */
export function clearSession(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(USER_KEY)
  purgeLegacyAuth()
}

/**
 * One-time hygiene: remove any leftover pre-2B-2 token from localStorage and expire the old JS cookie.
 * These values are only ever DELETED here — never read, never sent — so a stale token cannot leak.
 */
export function purgeLegacyAuth(): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY)
    document.cookie = `${LEGACY_COOKIE}=; path=/; Max-Age=0; SameSite=Lax`
  } catch {
    /* storage disabled — nothing to purge */
  }
}

/** True if a user profile is present. A UI convenience only; the backend re-checks the cookie on every request. */
export function isAuthenticated(): boolean {
  return Boolean(getUser())
}

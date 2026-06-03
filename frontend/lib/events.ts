'use client'
/**
 * Operational event tracking — fire-and-forget, zero UX impact.
 *
 * Guarantees:
 *  - Never throws, never blocks, never delays navigation
 *  - Deferred via requestIdleCallback (or setTimeout fallback)
 *  - keepalive:true so events survive page unloads / route changes
 *  - Debounced per event+entity (500ms) to prevent accidental spam
 */
import { getToken } from './session'

interface TrackPayload {
  event_type:  string
  event_scope: string
  entity_id?:  string
  visitor_id?: string
  metadata?:   Record<string, unknown>
}

// ── Visitor identity (anonymous funnel stitching) ─────────────────────────
const _VISITOR_KEY = 'bp_visitor_id'
const _ATTRIBUTION_KEY = 'bp_attribution'
const _ATTRIBUTION_EVENTS = new Set(['registration_completed', 'landing_page_viewed'])

/** Stable anonymous visitor id (UUID), persisted in localStorage. */
export function getVisitorId(): string {
  if (typeof window === 'undefined') return 'ssr'
  try {
    let id = localStorage.getItem(_VISITOR_KEY)
    if (!id) {
      id = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `v_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
      localStorage.setItem(_VISITOR_KEY, id)
    }
    return id
  } catch { return 'anon' }
}

/** Capture UTM params + referrer once, on first visit (first-touch attribution). */
export function captureAttribution(): void {
  if (typeof window === 'undefined') return
  try {
    if (localStorage.getItem(_ATTRIBUTION_KEY)) return
    const q = new URLSearchParams(window.location.search)
    const a: Record<string, string> = {}
    for (const k of ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term']) {
      const v = q.get(k); if (v) a[k] = v
    }
    if (document.referrer) a.referrer = document.referrer
    localStorage.setItem(_ATTRIBUTION_KEY, JSON.stringify(a))
  } catch {}
}

function getAttribution(): Record<string, string> {
  if (typeof window === 'undefined') return {}
  try { return JSON.parse(localStorage.getItem(_ATTRIBUTION_KEY) || '{}') } catch { return {} }
}

// Debounce registry: "event_type:entity_id" → last fire timestamp
const _last = new Map<string, number>()
const _DEBOUNCE_MS = 500

function _debounceKey(type: string, entityId?: string): string {
  return `${type}:${entityId ?? ''}`
}

function _isThrottled(type: string, entityId?: string): boolean {
  const key = _debounceKey(type, entityId)
  const now = Date.now()
  const last = _last.get(key) ?? 0
  if (now - last < _DEBOUNCE_MS) return true
  _last.set(key, now)
  return false
}

async function _send(payload: TrackPayload): Promise<void> {
  try {
    const token = getToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`

    await fetch('/api/events/track', {
      method:      'POST',
      keepalive:   true,
      credentials: 'include',
      headers,
      body:        JSON.stringify(payload),
    })
  } catch {
    // silent — tracking must never surface errors
  }
}

function _defer(payload: TrackPayload): void {
  if (typeof requestIdleCallback !== 'undefined') {
    requestIdleCallback(() => { _send(payload) }, { timeout: 2_000 })
  } else {
    setTimeout(() => { _send(payload) }, 0)
  }
}

/**
 * Track an operational event.
 * Call-and-forget — no await needed.
 */
export function trackEvent(
  eventType:  string,
  scope:      string,
  entityId?:  string,
  metadata?:  Record<string, unknown>,
): void {
  if (typeof window === 'undefined') return
  if (_isThrottled(eventType, entityId)) return
  const meta: Record<string, unknown> = { ...(metadata ?? {}) }
  if (_ATTRIBUTION_EVENTS.has(eventType)) Object.assign(meta, getAttribution())
  _defer({
    event_type:  eventType,
    event_scope: scope,
    entity_id:   entityId,
    visitor_id:  getVisitorId(),
    metadata:    Object.keys(meta).length ? meta : undefined,
  })
}

/* ── Funnel activation timing ──────────────────────────────────────────────
 * Anchor timestamps in localStorage so activation deltas (time_to_first_*)
 * are computed automatically and survive navigation. Each anchor is written
 * once (first occurrence wins) so deltas measure true time-to-first.
 */
export const FUNNEL_TS = {
  signup:        'bp_ts_signup',
  firstImport:   'bp_ts_first_import',
  firstInsight:  'bp_ts_first_insight',
} as const

/** Write a timestamp anchor once (no-op if already set). */
export function stampFunnel(key: string): void {
  if (typeof window === 'undefined') return
  try { if (!localStorage.getItem(key)) localStorage.setItem(key, String(Date.now())) } catch {}
}

/** Ms elapsed since an anchor, or undefined if the anchor is absent. */
export function elapsedSince(key: string): number | undefined {
  if (typeof window === 'undefined') return undefined
  try {
    const t = localStorage.getItem(key)
    if (!t) return undefined
    const ms = Date.now() - Number(t)
    return Number.isFinite(ms) && ms >= 0 ? ms : undefined
  } catch { return undefined }
}

/** True only the first time it's called for `key` (one-shot guard). */
export function firstTimeOnly(key: string): boolean {
  if (typeof window === 'undefined') return false
  try {
    if (localStorage.getItem(key)) return false
    localStorage.setItem(key, '1')
    return true
  } catch { return false }
}

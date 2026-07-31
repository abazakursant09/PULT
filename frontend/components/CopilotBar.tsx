'use client'

import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { X, Zap } from 'lucide-react'
import { api, type TodayItem } from '@/lib/api'
import { isAuthenticated } from '@/lib/session'
import { trackEvent } from '@/lib/events'

// ── Module-level cache ────────────────────────────────────────────────────────
// Survives soft navigation (module is loaded once per session).
// Server renders with null (no window), client populates on first fetch.
//
// One Morning Truth (A19): Copilot reads the canonical Today service (/api/today,
// the same source as the Dashboard feed and the Telegram top action) — NOT legacy
// /api/insights.

let _today:     TodayItem[] | null = null
let _fetchedAt  = 0
const _STALE_MS = 30_000
const _AE_COUNT_EVENT = 'ae-count-update'

function _isFresh(): boolean {
  return _today !== null && Date.now() - _fetchedAt < _STALE_MS
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// path segment → canonical contour(s) to prefer in that context
const _CTX: Record<string, string[]> = {
  'seo-cards':        ['seo'],
  'seo-lab':          ['seo'],
  'seo-intelligence': ['seo'],
  'finance':          ['advertising', 'pricing', 'growth'],
}

function _pick(items: TodayItem[], pathname: string): TodayItem | null {
  // items are already canonical, live, and priority-ordered by build_feed.
  if (!items.length) return null
  for (const [segment, contours] of Object.entries(_CTX)) {
    if (pathname.includes(segment)) {
      const match = items.find(i => contours.includes(i.contour))
      if (match) return match
    }
  }
  return items[0]   // highest-priority item (feed order preserved)
}

function _syncBadge(items: TodayItem[]) {
  // active-like live items (the feed already excludes resolved/dismissed)
  const cnt = items.filter(i => i.source_status !== 'acknowledged').length
  try { localStorage.setItem('ae_active_count', String(cnt)) } catch {}
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(_AE_COUNT_EVENT, { detail: cnt }))
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export function CopilotBar() {
  const router   = useRouter()
  const pathname = usePathname()

  // Initialize from module cache — instant on soft-nav, null on cold load
  const [item, setItem] = useState<TodayItem | null>(() =>
    _today ? _pick(_today, pathname) : null
  )
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    if (!isAuthenticated()) return

    // Apply current cache pick for new pathname (no flicker on nav)
    if (_today) {
      const pick = _pick(_today, pathname)
      setItem(pick)
      if (pick) {
        const dismissed = sessionStorage.getItem('copilot_dismissed')
        if (dismissed === pick.item_key) { setHidden(true); return }
        else setHidden(false)
      }
      if (_isFresh()) return   // skip network — cache is hot
    }

    // Background revalidation (silent)
    api.today.get()
      .then(r => {
        _today     = r.items
        _fetchedAt = Date.now()
        _syncBadge(r.items)

        const pick = _pick(r.items, pathname)
        setItem(pick)

        if (pick) {
          const dismissed = sessionStorage.getItem('copilot_dismissed')
          if (dismissed === pick.item_key) setHidden(true)
          else setHidden(false)
        }
      })
      .catch(() => {})
  // pathname drives context re-pick; item/hidden are output, not deps
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname])

  if (!item || hidden) return null
  if (pathname.includes('action-engine')) return null

  const title = item.what_happened || item.title || 'Рекомендация'
  const cta   = item.recommended_action || 'Открыть'

  function handleAction() {
    trackEvent('copilot_cta_clicked', pathname, item!.item_key, { contour: item!.contour })
    router.push('/dashboard')   // the canonical feed surface
  }

  function handleDismiss() {
    trackEvent('copilot_dismissed', pathname, item!.item_key, { contour: item!.contour })
    sessionStorage.setItem('copilot_dismissed', item!.item_key)
    setHidden(true)
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '0 20px', height: 42, flexShrink: 0,
      background: 'var(--bg)',
      borderBottom: '1px solid rgba(110,106,252,0.18)',
      borderLeft: '3px solid var(--violet)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        <Zap size={11} color="var(--violet-text)" />
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, overflow: 'hidden' }}>
        <span style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '60%' }}>
          {title}
        </span>
        {item.sku && (
          <span style={{ fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap', flexShrink: 0 }}>
            · {item.sku}
          </span>
        )}
      </div>

      <button
        onClick={handleAction}
        style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 700, color: 'var(--violet-text)', background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.25)', borderRadius: 6, padding: '5px 11px', cursor: 'pointer', whiteSpace: 'nowrap', maxWidth: '40%', overflow: 'hidden', textOverflow: 'ellipsis' }}
      >
        {cta} →
      </button>

      <button
        onClick={handleDismiss}
        style={{ flexShrink: 0, width: 22, height: 22, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)' }}
        title="Скрыть до следующей сессии"
      >
        <X size={12} />
      </button>
    </div>
  )
}

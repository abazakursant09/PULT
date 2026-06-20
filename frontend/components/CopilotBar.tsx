'use client'

import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { X, Zap } from 'lucide-react'
import { api, type InsightItem } from '@/lib/api'
import { getToken } from '@/lib/session'
import { trackEvent } from '@/lib/events'

// ── Module-level cache ────────────────────────────────────────────────────────
// Survives soft navigation (module is loaded once per session).
// Server renders with null (no window), client populates on first fetch.

let _insights:  InsightItem[] | null = null   // full list — for badge count
let _focused:   InsightItem[] | null = null   // preference-adapted curated list — for Copilot pick
let _fetchedAt  = 0
const _STALE_MS = 30_000
const _AE_COUNT_EVENT = 'ae-count-update'

function _isFresh(): boolean {
  return _insights !== null && Date.now() - _fetchedAt < _STALE_MS
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const _CTX: Record<string, string[]> = {
  'seo-cards':        ['seo_opportunity', 'rebuild'],
  'seo-lab':          ['seo_opportunity', 'rebuild'],
  'seo-intelligence': ['seo_opportunity', 'rebuild'],
  'finance':          ['sales_decline', 'margin', 'ad_spend'],
}

function _pick(focused: InsightItem[], pathname: string): InsightItem | null {
  // focused_insights is already: active, med/high confidence, deduped, preference-ranked.
  // Just do context matching on this pre-filtered list.
  const warnings = focused.filter(i => i.type === 'warning')
  if (!warnings.length) return null
  for (const [segment, keys] of Object.entries(_CTX)) {
    if (pathname.includes(segment)) {
      const match = warnings.find(i => keys.some(k => i.key.includes(k)))
      if (match) return match
    }
  }
  return warnings[0]
}

function _fmtLoss(ins: InsightItem): string | null {
  if (ins.estimated_monthly_loss_rub && ins.estimated_monthly_loss_rub > 0) {
    const k = Math.round(ins.estimated_monthly_loss_rub / 1000)
    return k > 0 ? `−${k}k ₽/мес` : null
  }
  if (ins.impact?.sign === 'negative' && ins.impact.estimate) {
    return ins.impact.estimate.replace('≈ ', '')
  }
  return null
}

function _syncBadge(insights: InsightItem[]) {
  const cnt = insights.filter(i => i.status === 'active' && i.type === 'warning').length
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
  const [insight, setInsight] = useState<InsightItem | null>(() =>
    _focused ? _pick(_focused, pathname) : null
  )
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    if (!getToken()) return

    // Apply current cache pick for new pathname (no flicker on nav)
    if (_focused) {
      const pick = _pick(_focused, pathname)
      setInsight(pick)
      if (pick) {
        const dismissed = sessionStorage.getItem('copilot_dismissed')
        if (dismissed === pick.key) { setHidden(true); return }
        else setHidden(false)
      }
      if (_isFresh()) return   // skip network — cache is hot
    }

    // Background revalidation (silent)
    api.actionEngine.getInsights()
      .then(r => {
        _insights  = r.insights
        _focused   = r.focused_insights
        _fetchedAt = Date.now()
        _syncBadge(r.insights)

        const pick = _pick(r.focused_insights, pathname)
        setInsight(pick)

        if (pick) {
          const dismissed = sessionStorage.getItem('copilot_dismissed')
          if (dismissed === pick.key) setHidden(true)
          else setHidden(false)
        }
      })
      .catch(() => {})
  // pathname drives context re-pick; insight/hidden are output, not deps
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname])

  if (!insight || hidden) return null
  if (pathname.includes('action-engine')) return null

  const primary = insight.actions.find(a => a.type === 'primary') ?? insight.actions[0]
  const loss    = _fmtLoss(insight)
  const trust   = insight.confidence_level === 'high'
    ? `${insight.confidence}% уверенность`
    : insight.confidence_level === 'medium' ? 'данных достаточно'
    : insight.is_demo ? 'DEMO' : null

  function handleAction() {
    if (!primary) return
    trackEvent('copilot_cta_clicked', pathname, insight!.key, { insight_type: insight!.type, scope: insight!.key.split(':')[0] })
    if (primary.params) {
      const q = new URLSearchParams(primary.params as Record<string, string>)
      router.push(`${primary.url}?${q}`)
    } else {
      router.push(primary.url)
    }
  }

  function handleDismiss() {
    trackEvent('copilot_dismissed', pathname, insight!.key, { insight_type: insight!.type })
    sessionStorage.setItem('copilot_dismissed', insight!.key)
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
        {insight.is_demo && (
          <span style={{ fontSize: 8.5, fontWeight: 800, letterSpacing: '0.10em', color: 'var(--violet-text)', background: 'rgba(110,106,252,0.12)', padding: '1px 5px', borderRadius: 3 }}>
            DEMO
          </span>
        )}
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, overflow: 'hidden' }}>
        <span style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '60%' }}>
          {insight.title}
        </span>
        {insight.product_name && (
          <span style={{ fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap', flexShrink: 0 }}>
            · {insight.product_name}
          </span>
        )}
        {loss && (
          <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--danger)', whiteSpace: 'nowrap', flexShrink: 0, background: 'rgba(239,68,68,0.08)', borderRadius: 20, padding: '1px 7px' }}>
            {loss}
          </span>
        )}
        {trust && !insight.is_demo && (
          <span style={{ fontSize: 10, color: 'var(--text-3)', whiteSpace: 'nowrap', flexShrink: 0 }}>
            {trust}
          </span>
        )}
      </div>

      {primary && (
        <button
          onClick={handleAction}
          style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 700, color: 'var(--violet-text)', background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.25)', borderRadius: 6, padding: '5px 11px', cursor: 'pointer', whiteSpace: 'nowrap' }}
        >
          {primary.label} →
        </button>
      )}

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

'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { ReviewOverview, ReviewSignal, ReviewProblem, ReviewAuditItem } from '@/lib/api'
import { ReviewOverviewCard } from './ReviewOverviewCard'
import { ReviewSignalsList } from './ReviewSignalsList'
import { ReviewProblemsList } from './ReviewProblemsList'
import { ReviewAuditHistory } from './ReviewAuditHistory'
import { ReviewAuditForm } from './ReviewAuditForm'

// Reputation contour container. Review signals are NOT listing-scoped on the
// backend (snapshot.listing_id is null), so we deliberately query user-wide and
// do NOT pass listingId as a filter — passing it would hide all signals.
// marketplace is provenance/context only. No reply drafting/sending anywhere.

type Tab = 'signals' | 'problems' | 'history'

const TABS: { k: Tab; l: string }[] = [
  { k: 'signals', l: 'Требуют реакции' },
  { k: 'problems', l: 'Проблемы' },
  { k: 'history', l: 'История проверок' },
]

export function ReviewAssistantPanel(
  { listingId, marketplace }: { listingId?: string; marketplace?: string },
) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [overview, setOverview] = useState<ReviewOverview | null>(null)
  const [signals, setSignals] = useState<ReviewSignal[]>([])
  const [problems, setProblems] = useState<ReviewProblem[]>([])
  const [audits, setAudits] = useState<ReviewAuditItem[]>([])
  const [tab, setTab] = useState<Tab>('signals')
  const [showForm, setShowForm] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        // user-wide reputation: listingId intentionally omitted (see note above)
        const [ov, sg, pr, au] = await Promise.all([
          api.reviewAssistant.getReviewOverview(undefined, marketplace),
          api.reviewAssistant.getReviewSignals(undefined, marketplace, 'active'),
          api.reviewAssistant.getReviewProblems(undefined, marketplace),
          api.reviewAssistant.getReviewAudits(undefined, marketplace),
        ])
        if (!alive) return
        setOverview(ov); setSignals(sg.items); setProblems(pr.items); setAudits(au.items)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [listingId, marketplace, reloadKey])

  const hasAudit = audits.length > 0
  const notEvaluated = overview?.total_not_evaluated ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка отзывов…</div>}
      {error && <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить отзывы: {error}</div>}

      {!loading && !error && (
        <>
          {overview && <ReviewOverviewCard overview={overview} />}

          {!hasAudit && (
            <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>
              Проверок ещё не было. Запустите проверку отзыва ниже.
            </div>
          )}
          {hasAudit && notEvaluated > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
              Часть отзывов не удалось оценить ({notEvaluated}) — это не значит, что проблем нет.
            </div>
          )}

          <div role="tablist" style={{ display: 'flex', gap: 6 }}>
            {TABS.map((t) => (
              <button key={t.k} role="tab" aria-selected={tab === t.k} onClick={() => setTab(t.k)} style={{
                fontSize: 12, padding: '5px 10px', borderRadius: 7, cursor: 'pointer',
                border: '1px solid var(--line)',
                background: tab === t.k ? 'var(--surface-h)' : 'transparent',
                color: tab === t.k ? 'var(--text)' : 'var(--text-3)',
              }}>{t.l}</button>
            ))}
          </div>

          {tab === 'signals' && <ReviewSignalsList signals={signals} />}
          {tab === 'problems' && <ReviewProblemsList problems={problems} />}
          {tab === 'history' && <ReviewAuditHistory audits={audits} />}

          <button onClick={() => setShowForm((v) => !v)} style={{
            alignSelf: 'flex-start', fontSize: 12, color: 'var(--text-2)', background: 'none',
            border: 'none', cursor: 'pointer', padding: '4px 0', textDecoration: 'underline',
          }}>{showForm ? 'Скрыть проверку отзыва' : 'Проверить отзыв'}</button>
          {showForm && (
            <ReviewAuditForm marketplace={marketplace}
              onDone={() => { setReloadKey((k) => k + 1) }} />
          )}
        </>
      )}
    </div>
  )
}

export default ReviewAssistantPanel

'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { GrowthOverview, GrowthSignal, GrowthProblem, GrowthAuditItem } from '@/lib/api'
import { GrowthOverviewCard } from './GrowthOverviewCard'
import { GrowthSignalsList } from './GrowthSignalsList'
import { GrowthProblemsList } from './GrowthProblemsList'
import { GrowthAuditHistory } from './GrowthAuditHistory'
import { GrowthAuditForm } from './GrowthAuditForm'

// Growth / Opportunity contour container. Growth signals ARE listing-scoped on
// the backend, so listingId is passed through to every read; marketplace is
// provenance/context. No fabricated index, no prediction, no rival data.

type Tab = 'signals' | 'problems' | 'history'

const TABS: { k: Tab; l: string }[] = [
  { k: 'signals', l: 'Возможности' },
  { k: 'problems', l: 'Найденное' },
  { k: 'history', l: 'История проверок' },
]

export function GrowthPanel(
  { listingId, marketplace, sku }: { listingId?: string; marketplace?: string; sku?: string },
) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [overview, setOverview] = useState<GrowthOverview | null>(null)
  const [signals, setSignals] = useState<GrowthSignal[]>([])
  const [problems, setProblems] = useState<GrowthProblem[]>([])
  const [audits, setAudits] = useState<GrowthAuditItem[]>([])
  const [tab, setTab] = useState<Tab>('signals')
  const [showForm, setShowForm] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const [ov, sg, pr, au] = await Promise.all([
          api.growth.getGrowthOverview(listingId, marketplace),
          api.growth.getGrowthSignals(listingId, marketplace, 'active'),
          api.growth.getGrowthProblems(listingId, marketplace),
          api.growth.getGrowthAudits(listingId, marketplace),
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
      {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка возможностей…</div>}
      {error && <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить возможности: {error}</div>}

      {!loading && !error && (
        <>
          {overview && <GrowthOverviewCard overview={overview} />}

          {!hasAudit && (
            <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>
              Проверок ещё не было. Запустите поиск возможностей роста ниже.
            </div>
          )}
          {hasAudit && notEvaluated > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
              Часть возможностей не удалось оценить ({notEvaluated}) — это не значит, что их нет.
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

          {tab === 'signals' && <GrowthSignalsList signals={signals} />}
          {tab === 'problems' && <GrowthProblemsList problems={problems} />}
          {tab === 'history' && <GrowthAuditHistory audits={audits} />}

          <button onClick={() => setShowForm((v) => !v)} style={{
            alignSelf: 'flex-start', fontSize: 12, color: 'var(--text-2)', background: 'none',
            border: 'none', cursor: 'pointer', padding: '4px 0', textDecoration: 'underline',
          }}>{showForm ? 'Скрыть поиск возможностей' : 'Найти возможности роста'}</button>
          {showForm && (
            <GrowthAuditForm listingId={listingId} marketplace={marketplace} sku={sku}
              onDone={() => { setReloadKey((k) => k + 1) }} />
          )}
        </>
      )}
    </div>
  )
}

export default GrowthPanel

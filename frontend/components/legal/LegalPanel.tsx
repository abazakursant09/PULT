'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { LegalOverview, LegalSignal, LegalAuditItem } from '@/lib/api'
import { LegalOverviewCard } from './LegalOverviewCard'
import { LegalSignalsList } from './LegalSignalsList'
import { LegalAuditHistory } from './LegalAuditHistory'
import { LegalAuditForm } from './LegalAuditForm'

// Legal Navigator container — a RECOMMENDATION screen, not a legal office. Signals
// are listing/subject-scoped. Advisory only: no rating, no verdict, no promise,
// no prediction, no money. not_evaluated is surfaced as "недостаточно данных",
// never as an all-clear.

type Tab = 'signals' | 'history'

const TABS: { k: Tab; l: string }[] = [
  { k: 'signals', l: 'Потенциальные риски' },
  { k: 'history', l: 'История проверок' },
]

export function LegalPanel(
  { listingId, marketplace, sku }: { listingId?: string; marketplace?: string; sku?: string },
) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [overview, setOverview] = useState<LegalOverview | null>(null)
  const [signals, setSignals] = useState<LegalSignal[]>([])
  const [audits, setAudits] = useState<LegalAuditItem[]>([])
  const [tab, setTab] = useState<Tab>('signals')
  const [showForm, setShowForm] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const [ov, sg, au] = await Promise.all([
          api.legalNavigator.getLegalOverview(listingId, marketplace),
          api.legalNavigator.getLegalSignals(listingId, marketplace),   // all statuses
          api.legalNavigator.getLegalAudits(listingId, marketplace),
        ])
        if (!alive) return
        setOverview(ov); setSignals(sg.items); setAudits(au.items)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [listingId, marketplace, reloadKey])

  const reload = () => setReloadKey((k) => k + 1)
  const hasAudit = audits.length > 0
  const notEvaluated = overview?.total_not_evaluated ?? 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка юридических рисков…</div>}
      {error && <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить: {error}</div>}

      {!loading && !error && (
        <>
          <LegalOverviewCard overview={overview} signals={signals} />

          {!hasAudit && (
            <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>
              Проверок ещё не было. Запустите проверку юридических рисков ниже.
            </div>
          )}
          {hasAudit && notEvaluated > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
              Часть требований не удалось проверить ({notEvaluated}) — недостаточно данных, это не значит, что всё в порядке.
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

          {tab === 'signals' && <LegalSignalsList signals={signals} onChanged={reload} />}
          {tab === 'history' && <LegalAuditHistory audits={audits} />}

          <button onClick={() => setShowForm((v) => !v)} style={{
            alignSelf: 'flex-start', fontSize: 12, color: 'var(--text-2)', background: 'none',
            border: 'none', cursor: 'pointer', padding: '4px 0', textDecoration: 'underline',
          }}>{showForm ? 'Скрыть проверку' : 'Проверить юридические риски'}</button>
          {showForm && (
            <LegalAuditForm listingId={listingId} marketplace={marketplace} sku={sku}
              onDone={reload} />
          )}
        </>
      )}
    </div>
  )
}

export default LegalPanel

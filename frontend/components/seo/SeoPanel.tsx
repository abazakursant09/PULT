'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { SeoOverview, SeoSignal, SeoProblem, SeoAuditItem } from '@/lib/api'
import { SeoOverviewCard } from './SeoOverviewCard'
import { SeoSignalsList } from './SeoSignalsList'
import { SeoProblemsList } from './SeoProblemsList'
import { SeoAuditHistory } from './SeoAuditHistory'
import { SeoManualAuditForm } from './SeoManualAuditForm'

/**
 * SeoPanel — SEO-блок карточки товара. Marketplace-agnostic: маркетплейс
 * приходит пропом и передаётся во все вызовы. Управляет загрузкой и состояниями
 * (loading / error / empty / «часть карточки не оценена»). Без SEO Score.
 */
type Tab = 'signals' | 'problems' | 'history'

const TABS: { k: Tab; l: string }[] = [
  { k: 'signals', l: 'Что требует внимания' },
  { k: 'problems', l: 'Проблемы' },
  { k: 'history', l: 'История' },
]

export function SeoPanel({ listingId, marketplace }: { listingId: string; marketplace: string }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [overview, setOverview] = useState<SeoOverview | null>(null)
  const [signals, setSignals] = useState<SeoSignal[]>([])
  const [problems, setProblems] = useState<SeoProblem[]>([])
  const [audits, setAudits] = useState<SeoAuditItem[]>([])
  const [tab, setTab] = useState<Tab>('signals')
  const [showForm, setShowForm] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const [ov, sg, pr, au] = await Promise.all([
          api.seo.getSeoOverview(listingId, marketplace),
          api.seo.getSeoSignals(listingId, marketplace, 'active'),
          api.seo.getSeoProblems(listingId, marketplace),
          api.seo.getSeoAudits(listingId, marketplace),
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

  const notEvaluated = audits[0]?.total_not_evaluated ?? 0
  const hasAudit = audits.length > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка SEO…</div>}
      {error && <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить SEO: {error}</div>}

      {!loading && !error && (
        <>
          {overview && <SeoOverviewCard overview={overview} notEvaluated={notEvaluated} />}

          {!hasAudit && (
            <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '6px 0' }}>
              Аудит карточки ещё не запускался — заполните данные ниже и проверьте.
            </div>
          )}
          {hasAudit && notEvaluated > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-3)', padding: '2px 2px' }}>
              Не удалось оценить часть карточки ({notEvaluated}) — не хватило данных или лимитов площадки.
            </div>
          )}

          <div className="s-ltabs" role="tablist" style={{ display: 'flex', gap: 6 }}>
            {TABS.map((t) => (
              <button key={t.k} role="tab" aria-selected={tab === t.k}
                className={`s-ltab${tab === t.k ? ' on' : ''}`} onClick={() => setTab(t.k)}>{t.l}</button>
            ))}
          </div>

          {tab === 'signals' && <SeoSignalsList signals={signals} />}
          {tab === 'problems' && <SeoProblemsList problems={problems} />}
          {tab === 'history' && <SeoAuditHistory audits={audits} />}

          <button onClick={() => setShowForm((v) => !v)} style={{
            alignSelf: 'flex-start', fontSize: 12, color: 'var(--text-2)', background: 'none',
            border: 'none', cursor: 'pointer', padding: '4px 0', textDecoration: 'underline',
          }}>{showForm ? 'Скрыть проверку карточки' : 'Проверить карточку вручную'}</button>
          {showForm && (
            <SeoManualAuditForm listingId={listingId} marketplace={marketplace}
              onDone={() => { setShowForm(false); setReloadKey((k) => k + 1) }} />
          )}
        </>
      )}
    </div>
  )
}

export default SeoPanel

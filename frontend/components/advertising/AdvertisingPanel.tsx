'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { AdvOverview, AdvSignal, AdvProblem, AdvAuditItem } from '@/lib/api'
import { AdvertisingOverviewCard } from './AdvertisingOverviewCard'
import { AdvertisingSignalsList } from './AdvertisingSignalsList'
import { AdvertisingProblemsList } from './AdvertisingProblemsList'
import { AdvertisingAuditHistory } from './AdvertisingAuditHistory'
import { AdvertisingAuditForm } from './AdvertisingAuditForm'

/**
 * AdvertisingPanel — рекламный блок карточки товара. Money-first, marketplace
 * passed on every call. Данные из импорта финансов; нет данных → честное
 * сообщение, а не ошибка. Состояния: loading / error / empty / finance_unavailable
 * / «часть рисков не оценена». Без score, без рекламного кабинета.
 */
type Tab = 'signals' | 'problems' | 'history'

const TABS: { k: Tab; l: string }[] = [
  { k: 'signals', l: 'Что требует внимания' },
  { k: 'problems', l: 'Проблемы' },
  { k: 'history', l: 'История' },
]

export function AdvertisingPanel({ listingId, marketplace }: { listingId: string; marketplace: string }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [overview, setOverview] = useState<AdvOverview | null>(null)
  const [signals, setSignals] = useState<AdvSignal[]>([])
  const [problems, setProblems] = useState<AdvProblem[]>([])
  const [audits, setAudits] = useState<AdvAuditItem[]>([])
  const [tab, setTab] = useState<Tab>('signals')
  const [showForm, setShowForm] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const [ov, sg, pr, au] = await Promise.all([
          api.advertising.getAdvertisingOverview(listingId, marketplace),
          api.advertising.getAdvertisingSignals(listingId, marketplace, 'active'),
          api.advertising.getAdvertisingProblems(listingId, marketplace),
          api.advertising.getAdvertisingAudits(listingId, marketplace),
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
      {loading && <div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка рекламы…</div>}
      {error && <div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить рекламу: {error}</div>}

      {!loading && !error && (
        <>
          {overview && <AdvertisingOverviewCard overview={overview} />}

          {!hasAudit && (
            <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '6px 0' }}>
              Рекламный аудит ещё не запускался — укажите SKU и проверьте.
            </div>
          )}
          {hasAudit && notEvaluated > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-3)', padding: '2px 2px' }}>
              Не удалось оценить часть рекламных рисков ({notEvaluated}) — не хватило данных или порогов площадки.
            </div>
          )}

          <div className="s-ltabs" role="tablist" style={{ display: 'flex', gap: 6 }}>
            {TABS.map((t) => (
              <button key={t.k} role="tab" aria-selected={tab === t.k} className={`s-ltab${tab === t.k ? ' on' : ''}`} onClick={() => setTab(t.k)}>{t.l}</button>
            ))}
          </div>

          {tab === 'signals' && <AdvertisingSignalsList signals={signals} />}
          {tab === 'problems' && <AdvertisingProblemsList problems={problems} />}
          {tab === 'history' && <AdvertisingAuditHistory audits={audits} />}

          <button onClick={() => setShowForm((v) => !v)} style={{ alignSelf: 'flex-start', fontSize: 12, color: 'var(--text-2)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 0', textDecoration: 'underline' }}>
            {showForm ? 'Скрыть проверку рекламы' : 'Проверить рекламу'}
          </button>
          {showForm && (
            <AdvertisingAuditForm listingId={listingId} marketplace={marketplace}
              onDone={() => { setShowForm(false); setReloadKey((k) => k + 1) }} />
          )}
        </>
      )}
    </div>
  )
}

export default AdvertisingPanel

'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type MarketplaceAccountOut, type MarketplaceStoreOut } from '@/lib/api'
import { ErrorState } from '@/components/system/ErrorState'
import { AddCabinetDialog } from './AddCabinetDialog'
import { AddYandexStoreDialog } from './AddYandexStoreDialog'
import { ArchiveStoreDialog } from './ArchiveStoreDialog'
import { CabinetGroup, marketplaceLabel } from './CabinetGroup'
import { LedgerShell } from './LedgerShell'

// The ledger of cabinets and stores.
//
// Everything on screen comes from GET /api/marketplace-accounts?include_stores=true — the only
// call that returns stores alongside their cabinet. Search, filtering and collapsing are local:
// a seller has tens of stores, not thousands, and a round trip per keystroke would be slower
// than the truth is worth.

type Filter = 'all' | 'active' | 'archived'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all',      label: 'Все' },
  { key: 'active',   label: 'Активные' },
  { key: 'archived', label: 'Архив' },
]

function cabinetsWord(n: number): string {
  if (n === 1) return 'кабинет'
  if (n >= 2 && n <= 4) return 'кабинета'
  return 'кабинетов'
}
function storesWord(n: number): string {
  if (n === 1) return 'магазин'
  if (n >= 2 && n <= 4) return 'магазина'
  return 'магазинов'
}

export function StoresLedger() {
  const [accounts, setAccounts] = useState<MarketplaceAccountOut[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [cabinetOpen, setCabinetOpen] = useState(false)
  const [storeFor, setStoreFor] = useState<MarketplaceAccountOut | null>(null)
  const [archiveFor, setArchiveFor] = useState<MarketplaceStoreOut | null>(null)
  const [restoringId, setRestoringId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      setAccounts(await api.marketplaceAccounts.list(true))
    } catch {
      setAccounts(null)
      setFailed(true)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const restore = useCallback(async (store: MarketplaceStoreOut) => {
    setRestoringId(store.id)
    try {
      await api.marketplaceAccounts.setStoreStatus(store.id, 'active')
      await load()
    } catch {
      setFailed(true)
    } finally {
      setRestoringId(null)
    }
  }, [load])

  const toggle = (id: string) => setCollapsed(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })

  // Filter + search applied to STORES, then cabinets with nothing left drop out. A cabinet is
  // kept when its own name matches, so searching a cabinet shows all of its stores.
  const visible = useMemo(() => {
    if (!accounts) return []
    const q = query.trim().toLowerCase()
    return accounts
      .map(account => {
        const cabinetHit = !q
          || (account.label ?? '').toLowerCase().includes(q)
          || marketplaceLabel(account.marketplace).toLowerCase().includes(q)
        const stores = (account.stores ?? []).filter(s => {
          if (filter === 'active'   && s.status !== 'active')   return false
          if (filter === 'archived' && s.status !== 'archived') return false
          return cabinetHit || s.label.toLowerCase().includes(q)
        })
        return { account, stores }
      })
      .filter(({ account, stores }) => {
        if (stores.length > 0) return true
        // An empty Yandex cabinet must stay visible — otherwise the seller cannot add its first
        // store. A WB/Ozon cabinet without its store means the row has nothing to show.
        const empty = (account.stores ?? []).length === 0
        return empty && filter === 'all' && !query.trim() && account.marketplace === 'yandex'
      })
  }, [accounts, query, filter])

  const totals = useMemo(() => {
    const stores = visible.flatMap(v => v.stores)
    return {
      cabinets: visible.length,
      stores: stores.length,
      archived: stores.filter(s => s.status === 'archived').length,
    }
  }, [visible])

  const empty = accounts !== null && accounts.length === 0

  return (
    <LedgerShell
      title="Магазины"
      action={!empty && (
        <button type="button" className="l-btn-ink" onClick={() => setCabinetOpen(true)}>
          Добавить магазин
        </button>
      )}
    >
      {failed && <ErrorState message="Не удалось загрузить магазины. Повторите попытку." onRetry={() => void load()} />}

      {!failed && accounts === null && (
        <p className="l-dim" style={{ padding: '32px 0' }}>Загружаем магазины…</p>
      )}

      {!failed && empty && (
        <div style={{ padding: '72px 0 0', maxWidth: '52ch' }}>
          <h2 className="l-serif" style={{ fontSize: 30, fontWeight: 400, margin: '0 0 12px' }}>
            Здесь появятся ваши магазины.
          </h2>
          <p className="l-dim" style={{ margin: '0 0 28px' }}>
            Добавьте первый магазин, чтобы загрузить товары и показатели.
          </p>
          <button type="button" className="l-btn-ink" onClick={() => setCabinetOpen(true)}>
            Добавить магазин
          </button>
        </div>
      )}

      {!failed && accounts !== null && !empty && (
        <>
          <div
            style={{
              display: 'flex', alignItems: 'center', gap: 22, flexWrap: 'wrap',
              borderTop: '2px solid var(--text)', borderBottom: '1px solid var(--line)', padding: '12px 0',
            }}
          >
            <label style={{ display: 'flex', alignItems: 'center', gap: 9, flex: 1, minWidth: 220, maxWidth: 380 }}>
              <span className="sr-only">Поиск по кабинету или магазину</span>
              <input
                className="l-input"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Поиск по кабинету или магазину"
                autoComplete="off"
                style={{ fontSize: 14.5 }}
              />
            </label>
            <div style={{ display: 'flex', marginLeft: 'auto' }}>
              {FILTERS.map((f, i) => (
                <button
                  key={f.key}
                  type="button"
                  className="l-link l-caps"
                  aria-pressed={filter === f.key}
                  onClick={() => setFilter(f.key)}
                  style={{
                    padding: '3px 13px',
                    color: filter === f.key ? 'var(--text)' : 'var(--text-3)',
                    borderRight: i < FILTERS.length - 1 ? '1px solid var(--line)' : undefined,
                  }}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          <div className="l-grid l-colhead l-caps">
            <span className="l-c1">Кабинет и магазин</span>
            <span className="l-c2">Маркетплейс</span>
            <span className="l-c3">Состояние</span>
            <span className="l-right">Действия</span>
          </div>

          {visible.length === 0 ? (
            <p className="l-dim" style={{ padding: '48px 0' }}>
              Ничего не найдено. Измените запрос или снимите фильтр.
            </p>
          ) : (
            <section className="l-ledger">
              {visible.map(({ account, stores }) => (
                <CabinetGroup
                  key={account.id}
                  account={account}
                  stores={stores}
                  collapsed={collapsed.has(account.id)}
                  onToggle={() => toggle(account.id)}
                  onAddStore={setStoreFor}
                  onArchive={setArchiveFor}
                  onRestore={store => void restore(store)}
                  restoringId={restoringId}
                />
              ))}
            </section>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap', paddingTop: 22 }}>
            <span className="l-dim">
              <b style={{ color: 'var(--text)', fontWeight: 500 }}>{totals.cabinets}</b> {cabinetsWord(totals.cabinets)}
              {' · '}
              <b style={{ color: 'var(--text)', fontWeight: 500 }}>{totals.stores}</b> {storesWord(totals.stores)}
              {totals.archived > 0 && <>{' · '}<b style={{ color: 'var(--text)', fontWeight: 500 }}>{totals.archived}</b> в архиве</>}
            </span>
            <span className="l-dim">Файл всегда загружается в конкретный магазин.</span>
          </div>
        </>
      )}

      <AddCabinetDialog
        open={cabinetOpen}
        onOpenChange={setCabinetOpen}
        onCreated={() => void load()}
      />
      {storeFor && (
        <AddYandexStoreDialog
          open
          onOpenChange={o => { if (!o) setStoreFor(null) }}
          accountId={storeFor.id}
          accountLabel={storeFor.label ?? marketplaceLabel(storeFor.marketplace)}
          onCreated={() => { setStoreFor(null); void load() }}
        />
      )}
      {archiveFor && (
        <ArchiveStoreDialog
          open
          onOpenChange={o => { if (!o) setArchiveFor(null) }}
          storeId={archiveFor.id}
          storeLabel={archiveFor.label}
          onArchived={() => { setArchiveFor(null); void load() }}
        />
      )}
    </LedgerShell>
  )
}

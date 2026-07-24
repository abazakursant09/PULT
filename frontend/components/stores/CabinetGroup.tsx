'use client'

import type { MarketplaceAccountOut, MarketplaceStoreOut } from '@/lib/api'
import { StoreRow } from './StoreRow'

// A cabinet and its stores.
//
// The shape follows the marketplace, because the marketplace is the fact: Wildberries and Ozon
// have exactly one store per cabinet, so cabinet and store are ONE line and there is no "add a
// store" action to offer. Yandex can hold many, so it becomes a collapsible group with its own
// action. The layout is the explanation — no sentence repeats it on every row.

const MP_LABEL: Record<string, string> = {
  wildberries: 'Wildberries',
  ozon:        'Ozon',
  yandex:      'Яндекс Маркет',
}
const SINGLE_STORE = new Set(['wildberries', 'ozon'])

export function marketplaceLabel(mp: string): string {
  return MP_LABEL[mp] ?? mp
}

function storesWord(n: number): string {
  if (n === 1) return 'магазин'
  if (n >= 2 && n <= 4) return 'магазина'
  return 'магазинов'
}

export function CabinetGroup({
  account, stores, collapsed, onToggle, onAddStore, onArchive, onRestore, restoringId,
}: {
  account: MarketplaceAccountOut
  stores: MarketplaceStoreOut[]
  collapsed: boolean
  onToggle: () => void
  onAddStore: (account: MarketplaceAccountOut) => void
  onArchive: (store: MarketplaceStoreOut) => void
  onRestore: (store: MarketplaceStoreOut) => void
  restoringId: string | null
}) {
  const mp = marketplaceLabel(account.marketplace)
  const label = account.label ?? mp
  const single = SINGLE_STORE.has(account.marketplace)

  // WB/Ozon: cabinet + its one store on a single line. The seller still sees where a file goes.
  if (single) {
    const store = stores[0]
    if (!store) return null
    return (
      <div className="l-group">
        <StoreRow
          store={store}
          cabinetLabel={label}
          marketplaceLabel={mp}
          hasConnection={account.has_connection}
          compact
          onArchive={onArchive}
          onRestore={onRestore}
          restoring={restoringId === store.id}
        />
      </div>
    )
  }

  return (
    <div className={`l-group${collapsed ? ' l-collapsed' : ''}`}>
      <div className="l-grid l-row">
        <div className="l-c1 l-wrap">
          <button
            type="button"
            className="l-link"
            onClick={onToggle}
            aria-expanded={!collapsed}
            style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}
          >
            <svg className="l-chev" width="10" height="10" viewBox="0 0 12 12" aria-hidden
                 style={{ stroke: 'var(--text-3)', fill: 'none', strokeWidth: 2, flex: 'none' }}>
              <path d="M3 4.5l3 3 3-3" />
            </svg>
            <span className="l-serif l-h2" style={{ color: 'var(--text)' }}>{label}</span>
            {account.has_connection && <span className="l-caps l-green">— API подключён</span>}
          </button>
        </div>
        <div className="l-c2 l-dim" style={{ fontSize: 14 }}>{mp}</div>
        <div className="l-c3 l-caps l-muted">{stores.length} {storesWord(stores.length)}</div>
        <div className="l-acts" />
      </div>

      {!collapsed && (
        <>
          {stores.map(s => (
            <StoreRow
              key={s.id}
              store={s}
              marketplaceLabel={mp}
              onArchive={onArchive}
              onRestore={onRestore}
              restoring={restoringId === s.id}
            />
          ))}
          {stores.length === 0 && (
            <p className="l-dim l-indent" style={{ padding: '12px 0', fontSize: 14 }}>
              В кабинете пока нет магазинов.
            </p>
          )}
          <div className="l-indent" style={{ padding: '12px 0 14px', borderBottom: '1px solid var(--line)' }}>
            <button type="button" className="l-link l-caps" onClick={() => onAddStore(account)}>
              + Добавить магазин в кабинет
            </button>
          </div>
        </>
      )}
    </div>
  )
}

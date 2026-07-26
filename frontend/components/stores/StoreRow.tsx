'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import type { MarketplaceStoreOut } from '@/lib/api'

// One store in the ledger.
//
// What a seller sees: the store's name, its marketplace, whether it is active or archived, and
// what they can do with it. What they never see: store_key, external ids, uuids — those are how
// PULT stores the row, not what the seller owns.

export function StoreRow({
  store, cabinetLabel, marketplaceLabel, hasConnection, compact, onArchive, onRestore, restoring,
}: {
  store: MarketplaceStoreOut
  /** Set for WB/Ozon, where the cabinet and its only store are one line. */
  cabinetLabel?: string
  marketplaceLabel: string
  hasConnection?: boolean
  compact?: boolean
  onArchive: (store: MarketplaceStoreOut) => void
  onRestore: (store: MarketplaceStoreOut) => void
  restoring?: boolean
}) {
  const archived = store.status === 'archived'
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!menuOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  return (
    <div className="l-grid l-row">
      <div className={`l-c1 l-wrap${compact ? '' : ' l-indent'}`}>
        {cabinetLabel && (
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <Link href={`/dashboard/stores/${store.id}`}
                  className="l-serif l-h2"
                  style={{ color: archived ? 'var(--text-3)' : 'var(--text)', textDecoration: 'none' }}>
              {cabinetLabel}
            </Link>
            {hasConnection && <span className="l-caps l-green">— API подключён</span>}
          </span>
        )}
        {cabinetLabel
          ? <span className="l-dim" style={{ display: 'block', fontSize: 14, marginTop: 2 }}>{store.label}</span>
          : (
            <Link href={`/dashboard/stores/${store.id}`}
                  style={{ fontSize: 16, color: archived ? 'var(--text-3)' : 'var(--text)', textDecoration: 'none' }}>
              {store.label}
            </Link>
          )}
      </div>

      <div className="l-c2 l-dim" style={{ fontSize: 14 }}>{cabinetLabel ? marketplaceLabel : ''}</div>

      <div className={`l-c3 l-caps ${archived ? 'l-oxide' : 'l-green'}`}>
        {archived ? 'В архиве' : 'Активен'}
      </div>

      <div className="l-acts" style={{ position: 'relative' }} ref={menuRef}>
        {archived ? (
          <button type="button" className="l-btn" onClick={() => onRestore(store)} disabled={restoring}>
            {restoring ? 'Восстанавливаем…' : 'Восстановить'}
          </button>
        ) : (
          <>
            <Link href={`/dashboard/stores/${store.id}/import`} className="l-btn" style={{ textDecoration: 'none' }}>
              Загрузить CSV
            </Link>
            <button
              type="button"
              className="l-link"
              aria-label={`Действия с магазином ${store.label}`}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(o => !o)}
              style={{ fontSize: 16, padding: '2px 4px' }}
            >
              ⋯
            </button>
            {menuOpen && (
              // Scales in from the trigger it belongs to, not from the middle of the screen.
              <div
                role="menu"
                style={{
                  position: 'absolute', top: 'calc(100% + 4px)', right: 0, zIndex: 6,
                  background: 'var(--bg)', border: '1px solid var(--rule-strong)',
                  minWidth: 208, padding: '4px 0', transformOrigin: 'top right',
                  boxShadow: '0 8px 24px rgba(20,22,26,.10)',
                }}
              >
                <button
                  type="button"
                  role="menuitem"
                  className="l-link"
                  onClick={() => { setMenuOpen(false); onArchive(store) }}
                  style={{ display: 'block', width: '100%', padding: '8px 14px', fontSize: 14, color: 'var(--ledger-oxide)' }}
                >
                  Архивировать магазин
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

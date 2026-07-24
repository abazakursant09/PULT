'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { api, type StoreRef } from '@/lib/api'
import { LedgerShell } from '@/components/stores/LedgerShell'
import { StoreImportsTable } from '@/components/stores/StoreImportsTable'
import { StoreProductsTable } from '@/components/stores/StoreProductsTable'
import { marketplaceLabel } from '@/components/stores/CabinetGroup'

// One store: what is in it, and what was loaded into it.
//
// There is no GET for a single store, so the header comes from the store reference that both
// catalog endpoints already return — one small request instead of a new backend contract.
//
// An archived store stays fully readable. Only the upload action disappears, because that is the
// one thing the backend refuses (409) for an archived store.

export default function StorePage({ params }: { params: { storeId: string } }) {
  const { storeId } = params
  const [store, setStore] = useState<StoreRef | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'missing' | 'failed'>('loading')
  const [restoring, setRestoring] = useState(false)

  const load = useCallback(async () => {
    setState('loading')
    try {
      const head = await api.marketplaceStores.imports(storeId, { page: 1, page_size: 1 })
      setStore(head.store)
      setState('ready')
    } catch (e) {
      setState(e instanceof Error && /не найден/i.test(e.message) ? 'missing' : 'failed')
    }
  }, [storeId])

  useEffect(() => { void load() }, [load])

  const restore = async () => {
    setRestoring(true)
    try {
      await api.marketplaceAccounts.setStoreStatus(storeId, 'active')
      await load()
    } finally {
      setRestoring(false)
    }
  }

  if (state === 'loading') {
    return (
      <LedgerShell crumbs={[{ label: 'Магазины', href: '/dashboard/stores' }]} title="Магазин">
        <p className="l-dim" style={{ padding: '24px 0' }}>Загружаем магазин…</p>
      </LedgerShell>
    )
  }

  if (state === 'missing' || state === 'failed') {
    return (
      <LedgerShell crumbs={[{ label: 'Магазины', href: '/dashboard/stores' }]} title="Магазин">
        <hr className="l-rule" />
        <p style={{ padding: '28px 0 8px', fontSize: 16, maxWidth: '48ch' }}>
          {state === 'missing'
            ? 'Магазин не найден. Возможно, он удалён или принадлежит другому аккаунту.'
            : 'Не удалось открыть магазин. Повторите попытку.'}
        </p>
        <div style={{ display: 'flex', gap: 12, paddingTop: 12 }}>
          {state === 'failed' && (
            <button type="button" className="l-btn" onClick={() => void load()}>Повторить</button>
          )}
          <Link href="/dashboard/stores" className="l-btn" style={{ textDecoration: 'none' }}>
            Вернуться к магазинам
          </Link>
        </div>
      </LedgerShell>
    )
  }

  const archived = store?.status === 'archived'

  return (
    <LedgerShell
      crumbs={[{ label: 'Магазины', href: '/dashboard/stores' }]}
      title={store?.label ?? 'Магазин'}
      action={!archived && (
        <Link href={`/dashboard/stores/${storeId}/import`} className="l-btn-ink" style={{ textDecoration: 'none' }}>
          Загрузить CSV
        </Link>
      )}
    >
      <hr className="l-rule" />
      <p className="l-dim" style={{ padding: '12px 0 0' }}>
        {marketplaceLabel(store?.marketplace ?? '')}
      </p>

      {archived && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', borderLeft: '2px solid var(--ledger-oxide)', padding: '10px 0 10px 14px', margin: '18px 0 0' }}>
          <span style={{ flex: 1, minWidth: 240 }}>
            Магазин в архиве. Данные доступны для чтения, новые файлы не принимаются.
          </span>
          <button type="button" className="l-btn" onClick={() => void restore()} disabled={restoring}>
            {restoring ? 'Восстанавливаем…' : 'Восстановить'}
          </button>
        </div>
      )}

      <h2 className="l-serif l-h2" style={{ padding: '34px 0 10px' }}>Товары магазина</h2>
      <StoreProductsTable storeId={storeId} />

      <h2 className="l-serif l-h2" style={{ padding: '40px 0 10px' }}>История загрузок</h2>
      <StoreImportsTable storeId={storeId} />
    </LedgerShell>
  )
}

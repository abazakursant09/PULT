'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type StoreProductsPage } from '@/lib/api'
import { ErrorState } from '@/components/system/ErrorState'

// Products present in ONE store.
//
// Deliberately no revenue, profit, stock or rating column: the store-aware API does not produce
// those numbers, and a column of dashes is a promise the product cannot keep. What IS true is
// that the product is placed in this store, and when it was first and last seen — so that is
// what the table says.

const PAGE_SIZE = 25

function fmtDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

const PLACEMENT_LABEL: Record<string, string> = {
  active:   'В магазине',
  detached: 'Откреплён',
  vanished: 'Пропал из отчётов',
}

export function StoreProductsTable({ storeId }: { storeId: string }) {
  const [data, setData] = useState<StoreProductsPage | null>(null)
  const [page, setPage] = useState(1)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async (p: number) => {
    setFailed(false)
    try {
      setData(await api.marketplaceStores.products(storeId, { page: p, page_size: PAGE_SIZE }))
    } catch {
      setData(null)
      setFailed(true)
    }
  }, [storeId])

  useEffect(() => { void load(page) }, [load, page])

  if (failed) {
    return <ErrorState message="Не удалось загрузить товары магазина." onRetry={() => void load(page)} />
  }
  if (data === null) {
    return <p className="l-dim" style={{ padding: '24px 0' }}>Загружаем товары…</p>
  }
  if (data.total === 0) {
    return (
      <div style={{ padding: '28px 0 8px', maxWidth: '48ch' }}>
        <p style={{ fontSize: 16, margin: '0 0 6px' }}>В этом магазине пока нет товаров.</p>
        <p className="l-dim" style={{ margin: 0 }}>Загрузите отчёт — товары появятся после импорта.</p>
      </div>
    )
  }

  return (
    <>
      <div
        className="l-caps l-colhead"
        style={{ display: 'grid', gridTemplateColumns: 'minmax(200px,1fr) 160px 150px 150px', gap: '0 16px' }}
      >
        <span>Товар</span><span>Артикул</span><span>Состояние</span><span>Последний раз замечен</span>
      </div>

      <section className="l-ledger">
        {data.items.map(item => (
          <div
            key={item.product_id}
            className="l-row l-grid"
            style={{ gridTemplateColumns: 'minmax(200px,1fr) 160px 150px 150px' }}
          >
            <div className="l-c1 l-wrap" style={{ fontSize: 16 }}>{item.name}</div>
            <div className="l-c2 l-num l-dim" style={{ fontSize: 13 }}>{item.sku ?? '—'}</div>
            <div className={`l-c3 l-caps ${item.placement_status === 'active' ? 'l-green' : 'l-muted'}`}>
              {PLACEMENT_LABEL[item.placement_status] ?? item.placement_status}
            </div>
            <div className="l-num l-dim" style={{ fontSize: 13 }}>{fmtDate(item.last_seen_at)}</div>
          </div>
        ))}
      </section>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, paddingTop: 16, flexWrap: 'wrap' }}>
        <span className="l-dim">Показано {data.items.length} из {data.total}</span>
        {data.pages > 1 && (
          <span style={{ display: 'flex', gap: 10, marginLeft: 'auto' }}>
            <button type="button" className="l-btn" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
              Назад
            </button>
            <span className="l-caps l-muted" style={{ alignSelf: 'center' }}>
              Стр. {data.page} из {data.pages}
            </span>
            <button type="button" className="l-btn" disabled={page >= data.pages} onClick={() => setPage(p => p + 1)}>
              Дальше
            </button>
          </span>
        )}
      </div>
    </>
  )
}

'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { api, type StoreImportsPage } from '@/lib/api'
import { ErrorState } from '@/components/system/ErrorState'

// Every upload that landed in THIS store, newest first.
//
// The link to the conflict screen appears only when the backend says there are unresolved
// conflict rows. There is no global conflict centre: a conflict belongs to the import that
// produced it, and that is the only way in.

const PAGE_SIZE = 25

export const IMPORT_TYPE_LABEL: Record<string, string> = {
  products:     'Товары',
  finance:      'Финансы',
  returns:      'Возвраты',
  card_content: 'Данные карточек товаров',
}

const STATUS_LABEL: Record<string, string> = {
  confirmed:  'Импортирован',
  pending:    'Не подтверждён',
  processing: 'В обработке',
  failed:     'Не выполнен',
  expired:    'Истёк',
}

function fmtDateTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export function StoreImportsTable({ storeId }: { storeId: string }) {
  const [data, setData] = useState<StoreImportsPage | null>(null)
  const [page, setPage] = useState(1)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async (p: number) => {
    setFailed(false)
    try {
      setData(await api.marketplaceStores.imports(storeId, { page: p, page_size: PAGE_SIZE }))
    } catch {
      setData(null)
      setFailed(true)
    }
  }, [storeId])

  useEffect(() => { void load(page) }, [load, page])

  if (failed) {
    return <ErrorState message="Не удалось загрузить историю загрузок." onRetry={() => void load(page)} />
  }
  if (data === null) {
    return <p className="l-dim" style={{ padding: '24px 0' }}>Загружаем историю…</p>
  }
  if (data.total === 0) {
    return (
      <p className="l-dim" style={{ padding: '28px 0 8px', fontSize: 16 }}>
        В этот магазин ещё ничего не загружали.
      </p>
    )
  }

  return (
    <>
      <div
        className="l-caps l-colhead"
        style={{ display: 'grid', gridTemplateColumns: 'minmax(200px,1fr) 190px 120px 110px 180px', gap: '0 16px' }}
      >
        <span>Файл</span><span>Тип отчёта</span><span>Дата загрузки</span><span>Строк</span><span>Итог</span>
      </div>

      <section className="l-ledger">
        {data.items.map(item => (
          <div
            key={item.import_id}
            className="l-row l-grid"
            style={{ gridTemplateColumns: 'minmax(200px,1fr) 190px 120px 110px 180px' }}
          >
            <div className="l-c1 l-wrap" style={{ fontSize: 15 }}>{item.filename}</div>
            <div className="l-c2 l-dim" style={{ fontSize: 14 }}>
              {IMPORT_TYPE_LABEL[item.import_type] ?? item.import_type}
            </div>
            <div className="l-num l-dim" style={{ fontSize: 13 }}>{fmtDateTime(item.created_at)}</div>
            <div className="l-num l-dim" style={{ fontSize: 13 }}>{item.imported_count} из {item.total_rows}</div>
            <div className="l-c3">
              {item.has_unresolved_conflicts ? (
                <Link href={`/dashboard/imports/${item.import_id}/conflicts`} className="l-caps l-oxide">
                  Не разобрано строк: {item.conflicts} — разобрать
                </Link>
              ) : (
                <span className="l-caps l-muted">{STATUS_LABEL[item.status] ?? item.status}</span>
              )}
            </div>
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

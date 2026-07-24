'use client'

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type MarketplaceAccountOut } from '@/lib/api'
import { ErrorState } from '@/components/system/ErrorState'
import { LedgerShell } from './LedgerShell'
import { marketplaceLabel } from './CabinetGroup'

// The obligatory first step of an import: choose the store.
//
// Since PULT-LAUNCH-1.4.2 a CSV always lands in ONE store, and the backend refuses an upload
// without it. So this screen exists to make that choice — and it deliberately has no file input,
// no drag-and-drop and no upload call. There is exactly one CSV flow in the product, and it
// lives at /dashboard/stores/[storeId]/import.
//
// Archived stores are not listed: they cannot receive a file, and offering them would be a
// promise the backend rejects with a 409.

export function StorePicker() {
  const [accounts, setAccounts] = useState<MarketplaceAccountOut[] | null>(null)
  const [failed, setFailed] = useState(false)

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

  const rows = useMemo(() => {
    if (!accounts) return []
    return accounts.flatMap(a =>
      (a.stores ?? [])
        .filter(s => s.status === 'active')
        .map(s => ({ store: s, cabinet: a.label ?? marketplaceLabel(a.marketplace), mp: marketplaceLabel(a.marketplace) })),
    )
  }, [accounts])

  const hasAnyStore = (accounts ?? []).some(a => (a.stores ?? []).length > 0)

  return (
    <LedgerShell title="Импорт данных">
      <p className="l-dim" style={{ margin: '0 0 4px', fontSize: 16 }}>
        Сначала выберите магазин, в который загружаете данные
      </p>
      <hr className="l-rule" style={{ marginTop: 14 }} />

      {failed && <ErrorState message="Не удалось загрузить магазины. Повторите попытку." onRetry={() => void load()} />}

      {!failed && accounts === null && (
        <p className="l-dim" style={{ padding: '32px 0' }}>Загружаем магазины…</p>
      )}

      {!failed && accounts !== null && rows.length === 0 && (
        <div style={{ padding: '56px 0 0', maxWidth: '52ch' }}>
          <h2 className="l-serif" style={{ fontSize: 26, fontWeight: 400, margin: '0 0 12px' }}>
            {hasAnyStore ? 'Все ваши магазины в архиве.' : 'Нет ни одного активного магазина.'}
          </h2>
          <p className="l-dim" style={{ margin: '0 0 26px' }}>
            {hasAnyStore
              ? 'Восстановите магазин, чтобы загрузить файл.'
              : 'Создайте магазин, чтобы загружать отчёты.'}
          </p>
          <Link href="/dashboard/stores" className="l-btn-ink" style={{ textDecoration: 'none' }}>
            Перейти к магазинам
          </Link>
        </div>
      )}

      {!failed && rows.length > 0 && (
        <section className="l-ledger" style={{ marginTop: 6 }}>
          {rows.map(({ store, cabinet, mp }) => (
            <div key={store.id} className="l-grid l-row">
              <div className="l-c1 l-wrap">
                <span style={{ fontSize: 16 }}>{store.label}</span>
                <span className="l-dim" style={{ display: 'block', fontSize: 14, marginTop: 2 }}>{cabinet}</span>
              </div>
              <div className="l-c2 l-dim" style={{ fontSize: 14 }}>{mp}</div>
              <div className="l-c3 l-caps l-green">Активен</div>
              <div className="l-acts">
                <Link href={`/dashboard/stores/${store.id}/import`} className="l-btn" style={{ textDecoration: 'none' }}>
                  Выбрать
                </Link>
              </div>
            </div>
          ))}
        </section>
      )}
    </LedgerShell>
  )
}

'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { api, type ImportConflictRow } from '@/lib/api'
import { LedgerShell } from '@/components/stores/LedgerShell'
import { ErrorState } from '@/components/system/ErrorState'

// Resolving the rows PULT could not match to a product on its own.
//
// Three actions, because the backend implements three. There is no per-row "keep / overwrite" —
// that choice belongs to the whole file and was already made at import. After every decision the
// list is re-read from the server, so what the seller sees is the stored state, not a guess.

type Action = 'link_existing' | 'create_new' | 'leave_unassigned'

export default function ConflictsPage() {
  const { importId } = useParams<{ importId: string }>()
  const [rows, setRows] = useState<ImportConflictRow[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [busyRow, setBusyRow] = useState<string | null>(null)
  const [choice, setChoice] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [resolvedAny, setResolvedAny] = useState(false)

  const load = useCallback(async () => {
    setFailed(false)
    try {
      setRows(await api.csvImport.conflicts(importId))
    } catch {
      setRows(null)
      setFailed(true)
    }
  }, [importId])

  useEffect(() => { void load() }, [load])

  const resolve = async (row: ImportConflictRow, action: Action) => {
    if (action === 'link_existing' && !choice[row.row_id]) {
      setError('Выберите товар, с которым связать строку.')
      return
    }
    setBusyRow(row.row_id); setError('')
    try {
      await api.csvImport.resolveConflict(importId, {
        row_id: row.row_id,
        action,
        ...(action === 'link_existing' ? { product_id: choice[row.row_id] } : {}),
      })
      setResolvedAny(true)
      await load()
    } catch {
      setError('Не удалось применить решение. Строка осталась без изменений.')
    } finally {
      setBusyRow(null)
    }
  }

  return (
    <LedgerShell crumbs={[{ label: 'Магазины', href: '/dashboard/stores' }]} title="Разбор конфликтов">
      <hr className="l-rule" />

      {failed && <ErrorState message="Не удалось загрузить конфликты этой загрузки." onRetry={() => void load()} />}

      {!failed && rows === null && <p className="l-dim" style={{ padding: '24px 0' }}>Загружаем строки…</p>}

      {!failed && rows !== null && rows.length === 0 && (
        <div style={{ padding: '30px 0 0', maxWidth: '52ch' }}>
          <p style={{ fontSize: 16, margin: '0 0 8px' }}>
            {resolvedAny ? 'Все строки этой загрузки разобраны.' : 'В этой загрузке нет неразобранных строк.'}
          </p>
          <Link href="/dashboard/stores" className="l-btn" style={{ textDecoration: 'none', display: 'inline-block', marginTop: 12 }}>
            Вернуться к магазинам
          </Link>
        </div>
      )}

      {!failed && rows !== null && rows.length > 0 && (
        <>
          <p className="l-dim" style={{ padding: '18px 0 0', maxWidth: '68ch' }}>
            PULT не смог однозначно сопоставить эти строки с товарами кабинета. Выберите, что сделать.
          </p>

          {error && <p className="l-oxide" role="alert" style={{ paddingTop: 14 }}>{error}</p>}

          <section className="l-ledger" style={{ marginTop: 20 }}>
            {rows.map(row => (
              <div key={row.row_id} className="l-row" style={{ display: 'grid', gap: 10 }}>
                <div className="l-wrap">
                  <span className="l-num" style={{ fontSize: 15 }}>{row.sku ?? '—'}</span>
                  {row.title && <span className="l-dim" style={{ marginLeft: 10 }}>{row.title}</span>}
                </div>

                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <label className="l-caps l-muted" htmlFor={`cand-${row.row_id}`}>Товар кабинета</label>
                  <select
                    id={`cand-${row.row_id}`}
                    className="l-input"
                    style={{ maxWidth: 360, fontSize: 14 }}
                    value={choice[row.row_id] ?? ''}
                    onChange={e => { setChoice(c => ({ ...c, [row.row_id]: e.target.value })); setError('') }}
                  >
                    <option value="">Не выбран</option>
                    {row.candidates.map(c => (
                      <option key={c.product_id} value={c.product_id}>
                        {c.name ?? c.sku ?? 'Без названия'}{c.sku ? ` · ${c.sku}` : ''}
                      </option>
                    ))}
                  </select>
                </div>

                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <button type="button" className="l-btn" disabled={busyRow === row.row_id}
                          onClick={() => void resolve(row, 'link_existing')}>
                    Связать с товаром
                  </button>
                  <button type="button" className="l-btn" disabled={busyRow === row.row_id}
                          onClick={() => void resolve(row, 'create_new')}>
                    Создать новый товар
                  </button>
                  <button type="button" className="l-btn" disabled={busyRow === row.row_id}
                          onClick={() => void resolve(row, 'leave_unassigned')}>
                    Оставить без товара
                  </button>
                </div>
              </div>
            ))}
          </section>

          <p className="l-dim" style={{ paddingTop: 18 }}>Осталось разобрать строк: {rows.length}</p>
        </>
      )}
    </LedgerShell>
  )
}

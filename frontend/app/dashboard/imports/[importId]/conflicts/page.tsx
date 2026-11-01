'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState, useRef } from 'react'
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
  const [uncertain, setUncertain] = useState('')
  const decisionPending = useRef(false)
  const readPending = useRef(false)
  const routeGeneration = useRef(0)
  const readGeneration = useRef(0)

  const load = useCallback(async (scope = routeGeneration.current) => {
    if (scope !== routeGeneration.current || readPending.current) return
    const request = ++readGeneration.current
    const ownsRead = () => scope === routeGeneration.current && request === readGeneration.current
    readPending.current = true
    setFailed(false)
    setRows(null)
    try {
      const next = await api.csvImport.conflicts(importId)
      if (ownsRead()) setRows(next)
    } catch {
      if (ownsRead()) {
        setRows(null)
        setFailed(true)
      }
    } finally {
      if (ownsRead()) readPending.current = false
    }
  }, [importId])

  useEffect(() => {
    const scope = ++routeGeneration.current
    decisionPending.current = false
    readPending.current = false
    setBusyRow(null); setChoice({}); setError(''); setUncertain(''); setResolvedAny(false)
    void load(scope)
    return () => {
      routeGeneration.current += 1
      readGeneration.current += 1
      readPending.current = false
      decisionPending.current = false
    }
  }, [load])

  const resolve = async (row: ImportConflictRow, action: Action) => {
    if (decisionPending.current || readPending.current) return
    const scope = routeGeneration.current
    if (action === 'link_existing' && !choice[row.row_id]) {
      setError('Выберите товар, с которым связать строку.')
      return
    }
    decisionPending.current = true
    setBusyRow(row.row_id); setError(''); setUncertain('')
    try {
      await api.csvImport.resolveConflict(importId, {
        row_id: row.row_id,
        action,
        ...(action === 'link_existing' ? { product_id: choice[row.row_id] } : {}),
      })
      if (scope !== routeGeneration.current) return
      setResolvedAny(true)
      await load(scope)
    } catch {
      if (scope !== routeGeneration.current) return
      setUncertain('Результат решения не подтверждён. Проверьте сохранённое состояние перед повторным решением.')
      await load(scope)
    } finally {
      if (scope === routeGeneration.current) {
        decisionPending.current = false
        setBusyRow(null)
      }
    }
  }

  return (
    <LedgerShell crumbs={[{ label: 'Магазины', href: '/dashboard/stores' }]} title="Разбор конфликтов">
      <hr className="l-rule" />
      {uncertain && <p className="l-oxide" role="alert" style={{ paddingTop: 14 }}>{uncertain}</p>}
      {error && <p className="l-oxide" role="alert" style={{ paddingTop: 14 }}>{error}</p>}

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
                    disabled={busyRow !== null}
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
                  <button type="button" className="l-btn" disabled={busyRow !== null}
                          onClick={() => void resolve(row, 'link_existing')}>
                    Связать с товаром
                  </button>
                  <button type="button" className="l-btn" disabled={busyRow !== null}
                          onClick={() => void resolve(row, 'create_new')}>
                    Создать новый товар
                  </button>
                  <button type="button" className="l-btn" disabled={busyRow !== null}
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

'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api, type MarketplaceConnectionOut } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { verdict, statusLabel, isBroken, requestErrorText, mpName } from './connectionText'

// CONNECTION-UI — read-only in Settings (PULT-LAUNCH-1.4.5I). Every NEW connection is now created on
// the «Магазины» page, bound to a chosen cabinet (marketplace_account_id). This section no longer
// offers a second, account-less create path: it only shows the status of existing connections and
// the two safe self-service actions that touch no key material — re-check and disconnect. The API
// key is never shown and never re-entered here; replacing a key happens in the store flow.

const FEEDBACKS = 'feedbacks'

function scopeStatus(conn: MarketplaceConnectionOut): string | undefined {
  return conn.scopes_verification?.find(s => s.scope === FEEDBACKS)?.verification_status
}

function ConnectionCard({ conn, onChanged }: {
  conn: MarketplaceConnectionOut
  onChanged: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [confirmOff, setConfirmOff] = useState(false)

  const revoked = conn.status === 'revoked'
  const state = scopeStatus(conn)
  const broken = isBroken(state)

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(''); setNote('')
    try {
      await fn()
      await onChanged()
    } catch (e) {
      setError(requestErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  const recheck = () => run(async () => {
    const res = await api.connections.verify(conn.id, FEEDBACKS)
    setNote(verdict(res.outcome).text)
  })

  const disconnect = () => run(async () => {
    await api.connections.remove(conn.id)
    setConfirmOff(false)
  })

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-[14px]" style={{ color: 'var(--text)' }}>
          {mpName(conn.marketplace)}
        </span>
        <Badge variant={revoked ? 'secondary' : broken ? 'danger' : state === 'verified' ? 'success' : 'neutral'}
          className="text-[11px] rounded-[6px]">
          {revoked ? 'Отключён' : statusLabel(state)}
        </Badge>
      </div>

      {revoked ? (
        <p className="text-[12px] mb-2" style={{ color: 'var(--text-3)' }}>
          Магазин отключён. Подключить его заново можно в разделе «Магазины».
        </p>
      ) : (
        <>
          {broken && (
            <p className="text-[12px] mb-2" style={{ color: 'var(--danger)' }}>
              Автоответы не будут работать, пока ключ не заменён в разделе «Магазины».
            </p>
          )}
          {note && <p className="text-[12px] mb-2" style={{ color: 'var(--text-2)' }}>{note}</p>}

          <div className="flex items-center gap-2 flex-wrap">
            <Button size="sm" variant="outline" loading={busy} onClick={recheck}>
              Повторить проверку
            </Button>
            {!confirmOff ? (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmOff(true)}>
                Отключить
              </Button>
            ) : (
              <span className="flex items-center gap-2">
                <span className="text-[12px]" style={{ color: 'var(--text-2)' }}>
                  Ключ будет удалён. Кабинет, магазины, товары и загруженные CSV сохранятся. Отключить?
                </span>
                <Button size="sm" variant="destructive" loading={busy} onClick={disconnect}>
                  Да, отключить
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmOff(false)}>
                  Отмена
                </Button>
              </span>
            )}
          </div>
        </>
      )}

      {error && <p className="text-[12px] mt-2" style={{ color: 'var(--danger)' }}>{error}</p>}
    </Card>
  )
}

export function ConnectionsSection() {
  const [conns, setConns] = useState<MarketplaceConnectionOut[] | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setConns(await api.connections.list())
      setError('')
    } catch (e) {
      setError(requestErrorText(e))
      setConns([])
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <section id="connections">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>
          Подключённые магазины
        </h2>
      </div>

      <div className="mb-3 rounded-[8px] p-3" style={{ background: 'var(--surface-2, #f5f5f4)' }}>
        <p className="text-[12px] mb-2" style={{ color: 'var(--text-2)' }}>
          Подключения к маркетплейсам управляются в разделе «Магазины» — там ключ привязывается к
          нужному кабинету, а не создаётся отдельно.
        </p>
        <Link href="/dashboard/stores">
          <Button size="sm">Перейти к магазинам</Button>
        </Link>
      </div>

      {conns === null ? (
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>Загрузка подключений…</p>
      ) : conns.length === 0 ? (
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
          Пока ни один магазин не подключён. Добавьте кабинет в разделе «Магазины».
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {conns.map(c => <ConnectionCard key={c.id} conn={c} onChanged={load} />)}
        </div>
      )}

      {error && <p className="text-[12px] mt-2" style={{ color: 'var(--danger)' }}>{error}</p>}
    </section>
  )
}

'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type MarketplaceConnectionOut } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConnectMarketplaceDialog } from './ConnectMarketplaceDialog'
import { verdict, statusLabel, isBroken, requestErrorText, mpName } from './connectionText'

// CONNECTION-UI — the seller's own view of their marketplace connections, with the four actions
// that make this self-service: connect, re-check, replace the key, disconnect.
//
// The card never claims a connection works just because it exists. `status === 'connected'` is set
// by the backend the moment a key is saved, before anything is verified, so what the seller sees
// here is the per-scope VERIFICATION verdict — including the honest "not checked" that Ozon
// feedbacks gets today, because there is no probe for it.

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
  const [replacing, setReplacing] = useState(false)

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
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
          Магазин отключён. Подключите его заново, чтобы снова получать отзывы.
        </p>
      ) : (
        <>
          {broken && (
            <p className="text-[12px] mb-2" style={{ color: 'var(--danger)' }}>
              Автоответы не будут работать, пока ключ не заменён.
            </p>
          )}
          {note && <p className="text-[12px] mb-2" style={{ color: 'var(--text-2)' }}>{note}</p>}

          <div className="flex items-center gap-2 flex-wrap">
            <Button size="sm" variant="outline" loading={busy} onClick={recheck}>
              Повторить проверку
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setReplacing(true)}>
              Заменить ключ
            </Button>
            {!confirmOff ? (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmOff(true)}>
                Отключить
              </Button>
            ) : (
              <span className="flex items-center gap-2">
                <span className="text-[12px]" style={{ color: 'var(--text-2)' }}>
                  Автоответы для этого магазина перестанут работать. Отключить?
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

      {/* Replacing a key is the same form as connecting — the backend upserts and resets the
          verification verdict for that scope, so a stale green tick can never survive. */}
      <ConnectMarketplaceDialog open={replacing} onOpenChange={setReplacing} onConnected={onChanged} />
    </Card>
  )
}

export function ConnectionsSection() {
  const [conns, setConns] = useState<MarketplaceConnectionOut[] | null>(null)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)

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
        {conns !== null && conns.length > 0 && (
          <Button size="sm" onClick={() => setAdding(true)}>Подключить ещё</Button>
        )}
      </div>

      {conns === null ? (
        <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>Загрузка подключений…</p>
      ) : conns.length === 0 ? (
        <div>
          <p className="text-[12px] mb-3" style={{ color: 'var(--text-3)' }}>
            Пока ни один магазин не подключён. Подключите Wildberries или Ozon, чтобы PULT получал отзывы.
          </p>
          <Button size="sm" onClick={() => setAdding(true)}>Подключить маркетплейс</Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {conns.map(c => <ConnectionCard key={c.id} conn={c} onChanged={load} />)}
        </div>
      )}

      {error && <p className="text-[12px] mt-2" style={{ color: 'var(--danger)' }}>{error}</p>}

      <ConnectMarketplaceDialog open={adding} onOpenChange={setAdding} onConnected={load} />
    </section>
  )
}

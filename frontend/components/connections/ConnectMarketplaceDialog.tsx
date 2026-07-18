'use client'

import { useState } from 'react'
import { api } from '@/lib/api'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { verdict, requestErrorText, mpName } from './connectionText'

// CONNECTION-UI — the seller enters a marketplace API key here, and nowhere else.
//
// The secret lives in component state for the duration of one request and is wiped straight after.
// It is never written to localStorage/sessionStorage, never put in a URL, never logged, and never
// read back from the server — the API does not return it.
//
// Saving is NOT proof that the key works: the backend marks a connection "connected" the moment it
// is stored. So we always follow the save with a real verification call and report exactly what
// came back — including "we could not check this", which is the honest answer for Ozon today.

const FEEDBACKS = 'feedbacks'

type Choice = 'wildberries' | 'ozon'

const CABINET_HINT: Record<Choice, string> = {
  wildberries: 'Личный кабинет → Настройки → Доступ к API → категория «Вопросы и отзывы»',
  ozon: 'Личный кабинет → Настройки → Seller API',
}

export function ConnectMarketplaceDialog({
  open, onOpenChange, onConnected,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onConnected: () => void | Promise<void>
}) {
  const [mp, setMp] = useState<Choice>('wildberries')
  const [token, setToken] = useState('')
  const [clientId, setClientId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ kind: string; text: string } | null>(null)

  const reset = () => {
    setToken(''); setClientId(''); setError(''); setDone(null); setBusy(false)
  }

  const close = (v: boolean) => {
    if (!v) reset()                     // the secret does not outlive the dialog
    onOpenChange(v)
  }

  const submit = async () => {
    setError('')
    if (!token.trim()) { setError('Введите API-ключ.'); return }
    if (mp === 'ozon' && !clientId.trim()) { setError('Для Ozon нужен Client-Id.'); return }

    setBusy(true)
    try {
      const conn = await api.connections.create({
        marketplace: mp,
        token: token.trim(),
        scope: FEEDBACKS,
        ozon_client_id: mp === 'ozon' ? clientId.trim() : null,
      })
      setToken(''); setClientId('')      // wipe as soon as the request is out

      // Saved. Now find out whether it actually works. A failure to CHECK is not a failure to
      // save — the connection exists either way, so a broken verify call must not read as "lost".
      let outcome = ''
      try {
        outcome = (await api.connections.verify(conn.id, FEEDBACKS)).outcome
      } catch {
        outcome = ''                     // falls through to the neutral "unknown" verdict
      }
      setDone(verdict(outcome))
      await onConnected()
    } catch (e) {
      setError(requestErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Подключить маркетплейс</DialogTitle>
        </DialogHeader>

        {done ? (
          <div className="space-y-4">
            <p className="text-[13px]" style={{ color: 'var(--text)' }}>{done.text}</p>
            <Button size="sm" onClick={() => close(false)}>Готово</Button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
              Ключ хранится в зашифрованном виде и никогда не показывается повторно.
            </p>

            <div className="flex gap-2 flex-wrap">
              {(['wildberries', 'ozon'] as Choice[]).map(m => (
                <Button key={m} size="sm" variant={mp === m ? 'default' : 'outline'}
                  disabled={busy} onClick={() => { setMp(m); setError('') }}>
                  {mpName(m)}
                </Button>
              ))}
              {/* Yandex has no review provider yet — offering it would create a dead connection. */}
              <Button size="sm" variant="ghost" disabled aria-disabled="true">
                Яндекс Маркет — скоро
              </Button>
            </div>

            {mp === 'ozon' && (
              <div className="space-y-1.5">
                <Label htmlFor="ozon-client-id">Client-Id</Label>
                <Input id="ozon-client-id" value={clientId} disabled={busy}
                  onChange={e => setClientId(e.target.value)} autoComplete="off" />
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="mp-token">{`API-ключ ${mpName(mp)}`}</Label>
              <Input id="mp-token" type="password" value={token} disabled={busy}
                onChange={e => setToken(e.target.value)} autoComplete="off" />
              <p className="text-[11px]" style={{ color: 'var(--text-3)' }}>{CABINET_HINT[mp]}</p>
            </div>

            {error && <p className="text-[12px]" style={{ color: 'var(--danger)' }}>{error}</p>}

            <div className="flex gap-2">
              <Button size="sm" loading={busy} onClick={submit}>Подключить</Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => close(false)}>Отмена</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

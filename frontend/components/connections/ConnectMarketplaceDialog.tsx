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

type Choice = 'wildberries' | 'ozon' | 'yandex'

const CHOICES: Choice[] = ['wildberries', 'ozon', 'yandex']

const CABINET_HINT: Record<Choice, string> = {
  wildberries: 'Личный кабинет → Настройки → Доступ к API → категория «Вопросы и отзывы»',
  ozon: 'Личный кабинет → Настройки → Seller API',
  // The key must be able to WRITE: publishing a reply needs the communication group, and a
  // read-only token would verify as missing the permission rather than fail later at publish time.
  yandex: 'Кабинет продавца → Настройки → API → доступ «Общение с покупателями» (не только чтение)',
}

export function ConnectMarketplaceDialog({
  open, onOpenChange, onConnected, marketplace, ozonClientId,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onConnected: () => void | Promise<void>
  /**
   * Replacing the key of an EXISTING connection. Passing it switches the dialog to edit mode and
   * pins the marketplace.
   *
   * Without this the dialog always opened on Wildberries, including when it was opened from an
   * Ozon or Yandex card. A seller who pasted their key without noticing the toggle overwrote a
   * different marketplace's connection entirely — silently, because the request was valid.
   */
  marketplace?: Choice
  /** Current Ozon Client-Id, prefilled so a key rotation does not require retyping it. */
  ozonClientId?: string | null
}) {
  const editing = marketplace != null
  const [mp, setMp] = useState<Choice>(marketplace ?? 'wildberries')
  const [token, setToken] = useState('')
  const [clientId, setClientId] = useState(ozonClientId ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ kind: string; text: string } | null>(null)

  // Reopening must return to the card's own marketplace, never drift back to the default — and the
  // secret must not survive a close. Only the non-secret Client-Id is restored.
  const reset = () => {
    setMp(marketplace ?? 'wildberries')
    setToken(''); setClientId(ozonClientId ?? ''); setError(''); setDone(null); setBusy(false)
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
          <DialogTitle>{editing ? 'Заменить ключ' : 'Подключить маркетплейс'}</DialogTitle>
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

            {/* Replacing a key is scoped to ONE connection, so the marketplace is a fact of the
                card, not a choice. Rendered as text rather than a disabled toggle: a greyed-out
                row of buttons reads as "you may pick, but not now", which is not what is true. */}
            {editing ? (
              <p className="text-[13px]" style={{ color: 'var(--text)' }}>
                Магазин: <strong>{mpName(mp)}</strong>
              </p>
            ) : (
              <div className="flex gap-2 flex-wrap">
                {CHOICES.map(m => (
                  <Button key={m} size="sm" variant={mp === m ? 'default' : 'outline'}
                    disabled={busy} onClick={() => { setMp(m); setError('') }}>
                    {mpName(m)}
                  </Button>
                ))}
              </div>
            )}

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
              {/* "Сохранить ключ", not "Заменить ключ": the card already carries that label, and
                  the confirm action inside the dialog should say what pressing it does. */}
              <Button size="sm" loading={busy} onClick={submit}>
                {editing ? 'Сохранить ключ' : 'Подключить'}
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => close(false)}>Отмена</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

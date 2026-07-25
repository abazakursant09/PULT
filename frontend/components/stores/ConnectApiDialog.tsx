'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api'

// Connecting an API key to a cabinet the seller already has (PULT-LAUNCH-1.4.5D).
//
// The marketplace is fixed by the cabinet — you cannot connect an Ozon key to a Wildberries
// cabinet, so there is no marketplace toggle here. The key is bound to THIS account_id, so the
// backend never mints a second cabinet.
//
// Saving is NOT connecting. The backend stores the key, then a real verify call decides the truth,
// and this dialog shows exactly which step it is on. "API проверен" appears only after verify
// succeeds — never on save. CSV stays available the whole time; connecting a key adds a source, it
// never takes the file path away.

const FEEDBACKS = 'feedbacks'

const MP_LABEL: Record<string, string> = {
  wildberries: 'Wildberries',
  ozon:        'Ozon',
  yandex:      'Яндекс Маркет',
}

type Phase = 'form' | 'saving' | 'verifying' | 'verified' | 'yandex_mapping' | 'error'

export function ConnectApiDialog({
  open, onOpenChange, marketplaceAccountId, marketplace, accountLabel, onConnected,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  marketplaceAccountId: string
  marketplace: string
  accountLabel: string
  onConnected: () => void | Promise<void>
}) {
  const [token, setToken] = useState('')
  const [clientId, setClientId] = useState('')
  const [phase, setPhase] = useState<Phase>('form')
  const [error, setError] = useState('')

  const isOzon = marketplace === 'ozon'
  const isYandex = marketplace === 'yandex'

  const reset = () => { setToken(''); setClientId(''); setPhase('form'); setError('') }
  const close = (v: boolean) => { if (!v) reset(); onOpenChange(v) }

  const submit = async () => {
    if (!token.trim()) { setError('Введите API-ключ.'); return }
    if (isOzon && !clientId.trim()) { setError('Для Ozon нужен Client-Id.'); return }
    setError(''); setPhase('saving')
    try {
      // Bound to the chosen cabinet. The secret leaves component state as soon as the request is out.
      const conn = await api.connections.create({
        marketplace,
        token: token.trim(),
        scope: FEEDBACKS,
        ozon_client_id: isOzon ? clientId.trim() : null,
        marketplace_account_id: marketplaceAccountId,
      })
      setToken(''); setClientId('')     // wipe immediately

      setPhase('verifying')
      const outcome = (await api.connections.verify(conn.id, FEEDBACKS)).outcome
      if (outcome === 'verified') {
        // Yandex still needs its campaigns mapped to stores — that is 1.4.5G. Say so honestly here;
        // do not claim data is flowing.
        setPhase(isYandex ? 'yandex_mapping' : 'verified')
        await onConnected()
      } else {
        setPhase('error')
        setError(outcomeText(outcome))
      }
    } catch (e) {
      setPhase('error')
      setError(e instanceof Error && /already connected/i.test(e.message)
        ? 'Этот ключ или кабинет уже подключён.'
        : 'Не удалось подключить API. Проверьте ключ и повторите.')
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent role="dialog" aria-modal aria-label="Подключить API"
                     className="ledger" style={{ background: 'var(--bg)', borderColor: 'var(--text)', borderRadius: 0 }}>
        <DialogHeader>
          <DialogTitle className="l-serif" style={{ fontSize: 26, fontWeight: 400 }}>Подключить API</DialogTitle>
          <DialogDescription style={{ color: 'var(--text-2)' }}>
            Кабинет «{accountLabel}» · {MP_LABEL[marketplace] ?? marketplace}. Ключ привяжется к этому
            кабинету. CSV остаётся доступным.
          </DialogDescription>
        </DialogHeader>

        {(phase === 'form' || phase === 'saving' || phase === 'error') && (
          <>
            {isOzon && (
              <div style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
                <label className="l-caps l-muted" htmlFor="api-clientid" style={{ display: 'block', marginBottom: 9 }}>
                  Client-Id
                </label>
                <input id="api-clientid" className="l-input" value={clientId}
                       onChange={e => { setClientId(e.target.value); setError('') }}
                       autoComplete="off" inputMode="numeric" />
              </div>
            )}
            <div style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
              <label className="l-caps l-muted" htmlFor="api-token" style={{ display: 'block', marginBottom: 9 }}>
                API-ключ
              </label>
              <input id="api-token" className="l-input" type="password" value={token}
                     onChange={e => { setToken(e.target.value); setError('') }}
                     autoComplete="off" />
              <p className="l-dim" style={{ fontSize: 13.5, marginTop: 9 }}>
                Ключ шифруется на сервере и никогда не показывается обратно.
              </p>
            </div>

            {error && <p className="l-oxide" role="alert" style={{ fontSize: 13.5, paddingTop: 14 }}>{error}</p>}

            <div style={{ display: 'flex', gap: 12, paddingTop: 22 }}>
              <button type="button" className="l-btn-ink" onClick={submit} disabled={phase === 'saving'}>
                {phase === 'saving' ? 'Ключ сохранён, проверяем…' : 'Подключить'}
              </button>
              <button type="button" className="l-btn" onClick={() => close(false)} disabled={phase === 'saving'}>
                Отмена
              </button>
            </div>
          </>
        )}

        {phase === 'verifying' && (
          <p style={{ padding: '18px 0', fontSize: 16 }}>Ключ сохранён, проверяем…</p>
        )}

        {phase === 'verified' && (
          <div style={{ padding: '18px 0' }}>
            <p className="l-green" style={{ fontSize: 18 }}>API проверен</p>
            <p className="l-dim" style={{ marginTop: 6 }}>
              Ключ подтверждён маркетплейсом. Загрузка данных через API появится в следующих
              обновлениях — сейчас данные загружаются файлом (CSV).
            </p>
            <button type="button" className="l-btn" style={{ marginTop: 16 }} onClick={() => close(false)}>Готово</button>
          </div>
        )}

        {phase === 'yandex_mapping' && (
          <div style={{ padding: '18px 0' }}>
            <p className="l-green" style={{ fontSize: 18 }}>API проверен. Требуется сопоставить магазины</p>
            <p className="l-dim" style={{ marginTop: 6 }}>
              У кабинета Яндекса несколько магазинов. Сопоставление появится в следующем обновлении;
              пока данные загружаются файлом (CSV).
            </p>
            <button type="button" className="l-btn" style={{ marginTop: 16 }} onClick={() => close(false)}>Готово</button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function outcomeText(outcome: string): string {
  switch (outcome) {
    case 'revoked':        return 'Ключ недействителен. Проверьте, что он не отозван.'
    case 'missing_scope':  return 'У ключа нет нужного доступа. Выдайте права и повторите.'
    case 'tariff_restricted': return 'Доступ ограничен тарифом маркетплейса.'
    case 'rate_limited':   return 'Маркетплейс временно ограничил проверку. Повторите позже.'
    default:               return 'Не удалось проверить ключ. Повторите позже.'
  }
}

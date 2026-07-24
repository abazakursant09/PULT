'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api, type MarketplaceStoreOut } from '@/lib/api'

// Adding a store to a YANDEX cabinet — a different action from creating a cabinet, with a
// different endpoint, so it gets its own dialog. WB/Ozon never reach this screen: their cabinet
// already has its one store and the UI does not offer the action at all.

export function AddYandexStoreDialog({
  open, onOpenChange, accountId, accountLabel, onCreated,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  accountId: string
  accountLabel: string
  onCreated: (store: MarketplaceStoreOut) => void
}) {
  const [label, setLabel] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const name = label.trim()
    if (!name) { setError('Укажите название магазина'); return }
    setSaving(true); setError('')
    try {
      const store = await api.marketplaceAccounts.createStore(accountId, { label: name })
      onCreated(store)
      onOpenChange(false)
      setLabel('')
    } catch (e) {
      // Yandex allows many stores, so "already has a store" is NOT a case that can happen here.
      // The only honest message is that the attempt failed. The single-store 409 text belongs to
      // WB/Ozon, and this dialog is never opened for them.
      setError(e instanceof Error && /уже есть магазин/i.test(e.message)
        ? 'В этом кабинете уже создан магазин'
        : 'Не удалось добавить магазин. Повторите попытку.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent role="dialog" aria-modal aria-label="Добавить магазин в кабинет" className="ledger" style={{ background: 'var(--bg)', borderColor: 'var(--text)', borderRadius: 0 }}>
        <DialogHeader>
          <DialogTitle className="l-serif" style={{ fontSize: 26, fontWeight: 400 }}>Добавить магазин в кабинет</DialogTitle>
          <DialogDescription style={{ color: 'var(--text-2)' }}>
            Кабинет «{accountLabel}» · Яндекс Маркет. Магазинов может быть сколько угодно.
          </DialogDescription>
        </DialogHeader>

        <div style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
          <label className="l-caps l-muted" htmlFor="ym-store-label" style={{ display: 'block', marginBottom: 9 }}>
            Название магазина
          </label>
          <input
            id="ym-store-label"
            className="l-input"
            value={label}
            onChange={e => { setLabel(e.target.value); setError('') }}
            placeholder="Москва — FBS"
            autoComplete="off"
          />
          <p className="l-dim" style={{ fontSize: 13.5, marginTop: 9 }}>
            Название видите только вы. Файл всегда загружается в конкретный магазин.
          </p>
        </div>

        {error && <p className="l-oxide" role="alert" style={{ fontSize: 13.5, paddingTop: 14 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 12, paddingTop: 22 }}>
          <button type="button" className="l-btn-ink" onClick={submit} disabled={saving}>
            {saving ? 'Добавляем…' : 'Добавить магазин'}
          </button>
          <button type="button" className="l-btn" onClick={() => onOpenChange(false)} disabled={saving}>
            Отмена
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

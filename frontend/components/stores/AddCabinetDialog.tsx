'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api, type MarketplaceAccountOut } from '@/lib/api'

// Creating a CABINET. One backend call, and what it produces differs by marketplace:
//
//   Wildberries / Ozon — the backend creates the cabinet AND its single store in one transaction.
//                        The seller is never asked to name a store, because there is no choice to
//                        make: one cabinet, one store, forever.
//   Yandex             — the cabinet is created empty; stores are added afterwards, one at a time.
//
// That difference is stated in the dialog rather than hidden, so the result is never a surprise.

const MARKETPLACES: { value: string; label: string }[] = [
  { value: 'wildberries', label: 'Wildberries' },
  { value: 'ozon',        label: 'Ozon' },
  { value: 'yandex',      label: 'Яндекс Маркет' },
]

const SINGLE_STORE = new Set(['wildberries', 'ozon'])

export function AddCabinetDialog({
  open, onOpenChange, onCreated, onConnectApi,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onCreated: (account: MarketplaceAccountOut) => void
  /** Called instead of a plain close when the seller chose to connect an API key to the new cabinet. */
  onConnectApi?: (account: MarketplaceAccountOut) => void
}) {
  const [marketplace, setMarketplace] = useState('wildberries')
  const [label, setLabel] = useState('')
  const [mode, setMode] = useState<'csv' | 'api'>('csv')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const name = label.trim()
    if (!name) { setError('Укажите название кабинета'); return }
    setSaving(true); setError('')
    try {
      const account = await api.marketplaceAccounts.create({ marketplace, label: name })
      onCreated(account)
      onOpenChange(false)
      setLabel('')
      // The cabinet exists either way; connecting a key is a second, honest step. Choosing "API"
      // hands the fresh account to the connect dialog — it never skips creating the cabinet.
      if (mode === 'api') onConnectApi?.(account)
    } catch {
      setError('Не удалось создать кабинет. Повторите попытку.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent role="dialog" aria-modal aria-label="Добавить кабинет" className="ledger" style={{ background: 'var(--bg)', borderColor: 'var(--text)', borderRadius: 0 }}>
        <DialogHeader>
          <DialogTitle className="l-serif" style={{ fontSize: 26, fontWeight: 400 }}>Добавить кабинет</DialogTitle>
          <DialogDescription style={{ color: 'var(--text-2)' }}>
            API-ключ не нужен. Кабинет можно вести на файлах и подключить ключ позже.
          </DialogDescription>
        </DialogHeader>

        <div style={{ padding: '4px 0 0' }}>
          <div style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
            <label className="l-caps l-muted" htmlFor="cab-mp" style={{ display: 'block', marginBottom: 9 }}>
              Маркетплейс
            </label>
            <select
              id="cab-mp"
              className="l-input"
              value={marketplace}
              onChange={e => setMarketplace(e.target.value)}
            >
              {MARKETPLACES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
            <p className="l-dim" style={{ fontSize: 13.5, marginTop: 9 }}>
              {SINGLE_STORE.has(marketplace)
                ? 'Wildberries и Ozon дают один магазин на кабинет — он создастся сам.'
                : 'Яндекс Маркет допускает несколько магазинов. Их вы добавите после создания кабинета.'}
            </p>
          </div>

          <div style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
            <label className="l-caps l-muted" htmlFor="cab-label" style={{ display: 'block', marginBottom: 9 }}>
              Название кабинета
            </label>
            <input
              id="cab-label"
              className="l-input"
              value={label}
              onChange={e => { setLabel(e.target.value); setError('') }}
              placeholder="Основной кабинет"
              autoComplete="off"
            />
            <p className="l-dim" style={{ fontSize: 13.5, marginTop: 9 }}>
              Название видите только вы. Оно подписывает загруженные файлы.
            </p>
          </div>

          <div style={{ padding: '16px 0', borderBottom: '1px solid var(--line)' }}>
            <span className="l-caps l-muted" style={{ display: 'block', marginBottom: 9 }}>Как получать данные</span>
            <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '6px 0', cursor: 'pointer' }}>
              <input type="radio" name="cab-mode" checked={mode === 'csv'} onChange={() => setMode('csv')}
                     style={{ marginTop: 4, accentColor: 'var(--text)' }} />
              <span><span style={{ fontSize: 15 }}>Работать через CSV</span>
                <span className="l-dim" style={{ display: 'block', fontSize: 13.5 }}>Загружать отчёты файлом.</span></span>
            </label>
            <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '6px 0', cursor: 'pointer' }}>
              <input type="radio" name="cab-mode" checked={mode === 'api'} onChange={() => setMode('api')}
                     style={{ marginTop: 4, accentColor: 'var(--text)' }} />
              <span><span style={{ fontSize: 15 }}>Подключить API</span>
                <span className="l-dim" style={{ display: 'block', fontSize: 13.5 }}>Ввести ключ после создания кабинета. CSV останется доступен.</span></span>
            </label>
          </div>
        </div>

        {error && <p className="l-oxide" role="alert" style={{ fontSize: 13.5, paddingTop: 14 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 12, paddingTop: 22 }}>
          <button type="button" className="l-btn-ink" onClick={submit} disabled={saving}>
            {saving ? 'Создаём…' : 'Добавить кабинет'}
          </button>
          <button type="button" className="l-btn" onClick={() => onOpenChange(false)} disabled={saving}>
            Отмена
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

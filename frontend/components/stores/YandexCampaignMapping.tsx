'use client'

import { useCallback, useEffect, useState } from 'react'
import { api, type CampaignOut, type MarketplaceStoreOut } from '@/lib/api'

// Yandex campaign → store mapping (PULT-LAUNCH-1.4.5G, surfaced in 1.4.5I).
//
// A Yandex cabinet holds several campaigns (stores). After the key verifies, the seller binds each
// campaign to a PULT store — an existing one, or a new one they name. Nothing is auto-linked by name
// and no store is created silently: the seller states which shape they want. Internal UUIDs are
// never shown; a campaign is identified by its official campaign id and a human label.

function CampaignRow({ connectionId, campaign, stores, onChanged }: {
  connectionId: string
  campaign: CampaignOut
  stores: MarketplaceStoreOut[]
  onChanged: () => void | Promise<void>
}) {
  const [mode, setMode] = useState<'idle' | 'existing' | 'new'>('idle')
  const [storeId, setStoreId] = useState('')
  const [label, setLabel] = useState(campaign.label ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const linked = campaign.link_state === 'linked'
  const linkedStore = linked ? stores.find(s => s.id === campaign.linked_store_id) : null
  const unlinkedStores = stores.filter(s => !s.external_store_id && s.status === 'active')

  const link = async (body: { store_id?: string; new_store_label?: string }) => {
    setBusy(true); setError('')
    try {
      await api.connections.linkCampaign(connectionId, { campaign_id: campaign.campaign_id, ...body })
      setMode('idle')
      await onChanged()
    } catch {
      setError('Не удалось связать магазин. Повторите.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="l-src-row">
      <div className="l-src-label" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
        <span>{campaign.label ?? `Магазин ${campaign.campaign_id}`}</span>
        <span className="l-dim" style={{ fontSize: 12 }}>ID магазина: {campaign.campaign_id}</span>
      </div>

      {linked ? (
        <span className="l-src-tag">Связан{linkedStore ? ` · ${linkedStore.label}` : ''}</span>
      ) : mode === 'idle' ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {unlinkedStores.length > 0 && (
            <button type="button" className="l-btn" disabled={busy} onClick={() => setMode('existing')}>
              Связать с магазином
            </button>
          )}
          <button type="button" className="l-btn-ink" disabled={busy} onClick={() => setMode('new')}>
            Создать новый магазин
          </button>
        </div>
      ) : mode === 'existing' ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select className="l-input" value={storeId} onChange={e => setStoreId(e.target.value)} aria-label="Магазин">
            <option value="">Выберите магазин</option>
            {unlinkedStores.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <button type="button" className="l-btn-ink" disabled={busy || !storeId}
                  onClick={() => void link({ store_id: storeId })}>Связать</button>
          <button type="button" className="l-btn" disabled={busy} onClick={() => setMode('idle')}>Отмена</button>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="l-input" value={label} onChange={e => setLabel(e.target.value)}
                 placeholder="Название магазина" aria-label="Название магазина" />
          <button type="button" className="l-btn-ink" disabled={busy}
                  onClick={() => void link({ new_store_label: label.trim() || (campaign.label ?? campaign.campaign_id) })}>
            Создать и связать
          </button>
          <button type="button" className="l-btn" disabled={busy} onClick={() => setMode('idle')}>Отмена</button>
        </div>
      )}
      {error && <p className="l-src-note l-src-note--err">{error}</p>}
    </div>
  )
}

export function YandexCampaignMapping({ connectionId, accountId, onChanged }: {
  connectionId: string
  accountId: string
  onChanged?: () => void | Promise<void>
}) {
  const [campaigns, setCampaigns] = useState<CampaignOut[] | null>(null)
  const [stores, setStores] = useState<MarketplaceStoreOut[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')

  const load = useCallback(async () => {
    try {
      const [camps, accounts] = await Promise.all([
        api.connections.campaigns(connectionId),
        api.marketplaceAccounts.list(true),
      ])
      const acc = accounts.find(a => a.id === accountId)
      setStores(acc?.stores ?? [])
      setCampaigns(camps)
      setState('ready')
      await onChanged?.()
    } catch {
      setState('failed')
    }
  }, [connectionId, accountId, onChanged])

  useEffect(() => { void load() }, [load])

  if (state === 'loading') return <p className="l-dim" style={{ padding: '10px 0' }}>Загружаем магазины Яндекса…</p>
  if (state === 'failed' || !campaigns) {
    return <p className="l-dim" style={{ padding: '10px 0' }}>Не удалось получить магазины Яндекса. Повторите позже.</p>
  }
  if (campaigns.length === 0) {
    return <p className="l-dim" style={{ padding: '10px 0' }}>Магазины в кабинете не найдены.</p>
  }

  const unmapped = campaigns.filter(c => c.link_state !== 'linked').length
  return (
    <div>
      <p className="l-dim" style={{ padding: '4px 0 12px', maxWidth: '60ch' }}>
        {unmapped > 0
          ? 'Магазин Яндекса найден, но ещё не связан с магазином PULT. Свяжите каждый магазин, чтобы получать по нему данные.'
          : 'Все магазины кабинета связаны.'}
      </p>
      {campaigns.map(c => (
        <CampaignRow key={c.campaign_id} connectionId={connectionId} campaign={c} stores={stores} onChanged={load} />
      ))}
    </div>
  )
}

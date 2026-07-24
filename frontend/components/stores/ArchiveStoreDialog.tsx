'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api'

// Archiving takes a store out of the import path, so it is confirmed. Restoring gives capability
// back and takes nothing away, so it is not — a confirmation there would be ceremony, not safety.
// The PATCH only fires after the seller confirms.

export function ArchiveStoreDialog({
  open, onOpenChange, storeId, storeLabel, onArchived,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  storeId: string
  storeLabel: string
  onArchived: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const confirm = async () => {
    setSaving(true); setError('')
    try {
      await api.marketplaceAccounts.setStoreStatus(storeId, 'archived')
      onArchived()
      onOpenChange(false)
    } catch {
      setError('Не удалось архивировать магазин. Состояние не изменилось.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent role="dialog" aria-modal aria-label="Архивировать магазин?" className="ledger" style={{ background: 'var(--bg)', borderColor: 'var(--text)', borderRadius: 0 }}>
        <DialogHeader>
          <DialogTitle className="l-serif" style={{ fontSize: 26, fontWeight: 400 }}>Архивировать магазин?</DialogTitle>
          <DialogDescription style={{ color: 'var(--text-2)' }}>
            Товары и история загрузок сохранятся. Новые файлы нельзя будет загружать, пока магазин
            не восстановлен.
          </DialogDescription>
        </DialogHeader>

        <p className="l-dim" style={{ fontSize: 14 }}>Магазин: {storeLabel}</p>

        {error && <p className="l-oxide" role="alert" style={{ fontSize: 13.5, paddingTop: 14 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 12, paddingTop: 22 }}>
          <button type="button" className="l-btn-ink" onClick={confirm} disabled={saving}>
            {saving ? 'Архивируем…' : 'Архивировать'}
          </button>
          <button type="button" className="l-btn" onClick={() => onOpenChange(false)} disabled={saving}>
            Отмена
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

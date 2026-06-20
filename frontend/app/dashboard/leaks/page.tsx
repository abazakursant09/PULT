'use client'
/**
 * ВКЛАДКА 4 — ЧТО СЪЕДАЕТ ПРИБЫЛЬ. Самая важная вкладка.
 * Фильтры-линзы: реклама / комиссия / акции / возвраты / логистика.
 * Сверху — общая сумма потерь в РУБЛЯХ (не проценты). Далее карточки товаров.
 */
import { useState } from 'react'
import { LEAK_LENSES, flatten, fmtRub, sumLoss, type LensKey } from '@/lib/pultProduct'
import { useCabinet } from '@/components/cabinet/useCabinet'
import { WorkspaceTab, EmptyCabinet } from '@/components/cabinet/WorkspaceTab'
import { LensFilterBar } from '@/components/cabinet/LensFilterBar'
import { SignalCard } from '@/components/cabinet/SignalCard'

export default function LeaksPage() {
  const { mode, setMode, products } = useCabinet()
  const [lens, setLens] = useState<LensKey | 'all'>('all')

  const counts: Record<string, number> = {}
  LEAK_LENSES.forEach(k => { counts[k] = flatten(products, [k]).length })

  const set = lens === 'all' ? LEAK_LENSES : [lens]
  const items = flatten(products, set)
  const loss = sumLoss(products, set)

  const summary = mode !== 'empty' ? (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderLeft: '4px solid var(--danger)', borderRadius: 14, padding: 18, marginBottom: 18 }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-3)', marginBottom: 6 }}>Под угрозой сейчас</div>
      <div style={{ fontSize: 30, fontWeight: 800, color: 'var(--danger)' }}>−{fmtRub(loss)}/мес</div>
    </div>
  ) : undefined

  return (
    <WorkspaceTab title="Что съедает прибыль" subtitle="Реклама · Комиссия · Акции · Возвраты · Логистика — в рублях"
      mode={mode} onMode={setMode} summary={summary}>
      {mode === 'empty' ? <EmptyCabinet /> : (
        <>
          <LensFilterBar lenses={LEAK_LENSES} active={lens} counts={counts} onChange={setLens} />
          {items.length ? items.map(it => <SignalCard key={it.product.id + it.lens} product={it.product} lens={it.lens} signal={it.signal} mode={mode} />)
            : <div style={{ color: 'var(--text-3)', fontSize: 13, padding: 24, textAlign: 'center' }}>Здесь прибыль не течёт.</div>}
        </>
      )}
    </WorkspaceTab>
  )
}

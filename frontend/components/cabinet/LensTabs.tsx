'use client'
import { PRODUCT_TABS, LENS, fmtRub, type Mode, type ProductWithLenses } from '@/lib/pultProduct'
import { SignalCard } from './SignalCard'

/**
 * LensTabs — фильтр ОДНОГО товара по линзам (Прибыль/Реклама/Цены/Акции/SEO/Отзывы/Риски/Юрист).
 * Таб 'pribyl' = P&L-панель. Остальные = SignalCard по присутствующим сигналам линзы.
 * active управляется через ?lens= (controlled). Переиспользуем SignalCard как атом.
 */
export function LensTabs({
  product, active, mode, onChange,
}: {
  product: ProductWithLenses; active: string; mode: Mode; onChange: (key: string) => void
}) {
  const tab = PRODUCT_TABS.find(t => t.key === active) ?? PRODUCT_TABS[0]
  const signals = tab.pnl ? [] : tab.lenses
    .map(k => ({ lens: k, signal: product.lenses[k] }))
    .filter((x): x is { lens: typeof x.lens; signal: NonNullable<typeof x.signal> } => !!x.signal)

  return (
    <div>
      {/* tab bar */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', borderBottom: '1px solid var(--line)', marginBottom: 16 }}>
        {PRODUCT_TABS.map(t => {
          const has = t.pnl || t.lenses.some(k => product.lenses[k])
          const on = t.key === active
          return (
            <button key={t.key} onClick={() => onChange(t.key)} style={{
              padding: '11px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
              background: 'none', border: 'none', borderBottom: `2px solid ${on ? 'var(--violet)' : 'transparent'}`,
              color: on ? 'var(--text)' : has ? 'var(--text-2)' : 'var(--text-3)',
            }}>
              {t.label}
            </button>
          )
        })}
      </div>

      {/* content */}
      {tab.pnl ? (
        <PnlPanel product={product} />
      ) : signals.length ? (
        signals.map(s => (
          <SignalCard key={s.lens} product={product} lens={s.lens} signal={s.signal} mode={mode} />
        ))
      ) : (
        <div style={{ border: '1px dashed var(--line)', borderRadius: 12, padding: 26, textAlign: 'center', color: 'var(--text-3)', fontSize: 13 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>В этой линзе всё в порядке</div>
          По «{LENS[tab.lenses[0]]?.label ?? tab.label}» проблем нет — действие не требуется.
        </div>
      )}
    </div>
  )
}

/** P&L товара — факты денег (не график ради графика, строки эффекта). */
function PnlPanel({ product }: { product: ProductWithLenses }) {
  // разложение прибыли из линз-потерь (где известен effect_rub) + итог
  const lines: { label: string; value: number }[] = [
    { label: 'Прибыль · 30 дней', value: product.profit },
  ]
  ;(['reklama', 'komissiya', 'akcii', 'vozvraty', 'logistika'] as const).forEach(k => {
    const s = product.lenses[k]
    if (s && s.severity !== 'green') lines.push({ label: `Потенциал по «${LENS[k].label}»`, value: s.effect_rub })
  })
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: '6px 18px' }}>
      {lines.map((l, i) => (
        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0', borderBottom: i < lines.length - 1 ? '1px solid var(--line)' : 'none' }}>
          <span style={{ fontSize: 13, color: i === 0 ? 'var(--text)' : 'var(--text-2)', fontWeight: i === 0 ? 700 : 400 }}>{l.label}</span>
          <span style={{ fontSize: i === 0 ? 18 : 14, fontWeight: 800, color: l.value >= 0 ? 'var(--success)' : 'var(--danger)' }}>
            {l.value >= 0 ? '+' : '−'}{fmtRub(Math.abs(l.value))}
          </span>
        </div>
      ))}
    </div>
  )
}

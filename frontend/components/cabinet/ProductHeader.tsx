'use client'
import { fmtRub, type ProductWithLenses } from '@/lib/pultProduct'

const STATUS: Record<ProductWithLenses['status'], { t: string; c: string; b: string }> = {
  ok:   { t: 'здоров',   c: 'var(--success)', b: 'var(--success-dim)' },
  warn: { t: 'внимание', c: 'var(--warning)', b: 'var(--warning-dim)' },
  risk: { t: 'под риском',c: 'var(--danger)',  b: 'var(--danger-dim)' },
}

/** ProductHeader — шапка карточки товара: фото·название·прибыль·рейтинг·статус. */
export function ProductHeader({ product }: { product: ProductWithLenses }) {
  const st = STATUS[product.status]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 14, marginBottom: 18,
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18,
    }}>
      <div style={{ width: 54, height: 54, borderRadius: 12, background: 'var(--surface-h)', border: '1px solid var(--line)', display: 'grid', placeItems: 'center', fontSize: 26, flex: '0 0 auto' }}>
        {product.photo}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 17, fontWeight: 800, color: 'var(--text)' }}>{product.name}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4, fontSize: 12, color: 'var(--text-3)' }}>
          <span>{product.mp}</span>
          <span>★ {product.rating.toFixed(1)}</span>
          <span style={{ fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', padding: '3px 8px', borderRadius: 5, background: st.b, color: st.c }}>{st.t}</span>
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>прибыль · 30 дн</div>
        <div style={{ fontSize: 24, fontWeight: 800, color: product.profit >= 0 ? 'var(--success)' : 'var(--danger)' }}>
          {product.profit >= 0 ? '+' : '−'}{fmtRub(Math.abs(product.profit))}
        </div>
      </div>
    </div>
  )
}

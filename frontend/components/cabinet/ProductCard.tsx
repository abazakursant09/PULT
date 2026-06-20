'use client'
import Link from 'next/link'
import { CARD_LENSES, LENS, fmtRub, worstLens, type ProductWithLenses } from '@/lib/pultProduct'
import { SEV } from './severity'

/**
 * ProductCard — сшивка линз. Товар целиком в одной карточке:
 * фото · название · прибыль · рейтинг · статус + линзы (Реклама/Цена/Отзывы/SEO/Документы).
 * Кнопка «Решить главное» → роутит в худшую линзу карточки товара.
 */
export function ProductCard({ product }: { product: ProductWithLenses }) {
  const top = worstLens(product)
  const topSev = top ? SEV[product.lenses[top]!.severity] : SEV.green

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)',
      borderLeft: `4px solid ${topSev.color}`, borderRadius: 14, padding: 18, marginBottom: 14,
    }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 14 }}>
        <div style={thumb}>{product.photo}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--text)' }}>{product.name}</div>
          <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{product.mp} · ★ {product.rating.toFixed(1)}</div>
        </div>
        <div style={{ fontSize: 20, fontWeight: 800, color: product.profit >= 0 ? 'var(--success)' : 'var(--danger)' }}>
          {product.profit >= 0 ? '+' : '−'}{fmtRub(Math.abs(product.profit))}/мес
        </div>
      </div>

      {/* линзы — статус по каждой */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10, marginBottom: 14 }}>
        {CARD_LENSES.map(k => {
          const s = product.lenses[k]
          const sev = s ? SEV[s.severity] : SEV.green
          return (
            <div key={k} style={{ background: 'var(--surface-h)', border: '1px solid var(--line)', borderRadius: 11, padding: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 700, color: 'var(--text)', marginBottom: 5 }}>
                {LENS[k].icon} {LENS[k].label}
                <span style={{ marginLeft: 'auto', fontSize: 9.5, fontWeight: 700, padding: '2px 7px', borderRadius: 5, background: sev.dim, color: sev.color }}>
                  {s ? sev.label : 'ок'}
                </span>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.4 }}>
                {s ? s.problem : 'проблем нет'}
              </div>
            </div>
          )
        })}
      </div>

      {/* решение главного */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <Link href={`/dashboard/products/${product.id}${top ? `?lens=${top}` : ''}`} style={btnV}>
          {top ? `Решить главное (${LENS[top].label}) →` : 'Открыть товар →'}
        </Link>
        <Link href={`/dashboard/products/${product.id}`} style={btnGhost}>Все действия</Link>
      </div>
    </div>
  )
}

const thumb: React.CSSProperties = {
  width: 42, height: 42, borderRadius: 10, background: 'var(--surface-h)',
  border: '1px solid var(--line)', display: 'grid', placeItems: 'center', fontSize: 19, flex: '0 0 auto',
}
const btnV: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 700,
  padding: '10px 17px', borderRadius: 9, textDecoration: 'none', background: 'var(--violet)', color: '#fff',
}
const btnGhost: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 700,
  padding: '10px 17px', borderRadius: 9, textDecoration: 'none',
  background: 'transparent', border: '1px solid var(--line)', color: 'var(--text-2)',
}

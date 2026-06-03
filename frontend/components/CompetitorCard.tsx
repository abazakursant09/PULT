'use client'

import { useState } from 'react'
import { Star, Users, ShoppingCart, ArrowUpRight, X, TrendingDown, TrendingUp, Package, AlertTriangle } from 'lucide-react'
import { type Competitor } from '@/lib/api'

const SIG: Record<string, { badge: string; label: string }> = {
  direct:      { badge: 'badge badge-direct',      label: 'Прямой'    },
  significant: { badge: 'badge badge-significant', label: 'Значимый'  },
  minor:       { badge: 'badge badge-minor',        label: 'Незначит.' },
}

const MP: Record<string, string> = {
  wildberries:   'WB',
  ozon:          'Ozon',
  yandex_market: 'ЯМ',
}

function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0 }
  return Math.abs(h)
}

function stubDossier(c: Competitor) {
  const h = hash(c.id + c.competitor_name)
  const salesUnits  = 50 + (h % 450)
  const salesRub    = salesUnits * c.price
  const shareChange = (h % 21) - 10

  const ALL_CONCLUSIONS = [
    'Демпингует', 'Уходит из ниши', 'Слабый визуал',
    'Проблемы с отзывами', 'Активно растёт', 'Слабое SEO',
    'Нет видео-контента', 'Низкий рекламный бюджет',
  ]
  const conclusions = [
    ALL_CONCLUSIONS[h % ALL_CONCLUSIONS.length],
    ALL_CONCLUSIONS[(h * 7 + 3) % ALL_CONCLUSIONS.length],
  ]

  const baseProducts = [
    { name: 'Базовая модель',  price: Math.round(c.price * 0.82) },
    { name: 'Премиум версия',  price: Math.round(c.price * 1.35) },
    { name: 'Эконом вариант',  price: Math.round(c.price * 0.55) },
    { name: 'Комплект 2 шт.',  price: Math.round(c.price * 1.75) },
    { name: 'Цветной вариант', price: Math.round(c.price * 1.08) },
  ]
  const productCount = 3 + (h % 3)
  const products = baseProducts.slice(0, productCount).map((p, i) => ({
    ...p,
    rating: Math.min(5, 3.7 + ((h >> (i * 3)) & 0xf) / 10),
  }))

  return { salesUnits, salesRub, shareChange, conclusions, products }
}

function DossierModal({ competitor: c, onClose }: { competitor: Competitor; onClose: () => void }) {
  const d   = stubDossier(c)
  const sig = SIG[c.significance] ?? SIG.minor

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,0.35)',  }} />
      <div
        className="card p-6 relative w-full max-w-lg animate-slide-up overflow-y-auto"
        style={{ maxHeight: '90vh' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="card-line" />

        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-5">
          <div>
            <div className="flex items-center gap-2 mb-1.5">
              <span className={sig.badge}>{sig.label}</span>
              <span className="mono text-[11px]" style={{ color: 'rgba(0,0,0,0.38)' }}>
                #{c.rank} · {MP[c.marketplace] ?? c.marketplace}
              </span>
            </div>
            <h2 className="font-bold text-base leading-snug" style={{ color: '#202124' }}>
              {c.competitor_name}
            </h2>
            <p className="mono text-xl font-bold mt-1" style={{ color: '#202124' }}>
              {c.price.toLocaleString('ru-RU')} ₽
            </p>
          </div>
          <button onClick={onClose} className="btn btn-ghost shrink-0" style={{ padding: 7, width: 32, height: 32 }}>
            <X size={14} />
          </button>
        </div>

        <div className="divider mb-5" />

        {/* Показатели эффективности */}
        <div className="mb-5">
          <p className="label mb-3">Показатели эффективности</p>
          <div className="grid grid-cols-3 gap-3">
            <div className="stat-card" style={{ padding: '14px 16px' }}>
              <div className="mono text-base font-semibold leading-none mb-1" style={{ color: '#202124' }}>
                {d.salesUnits.toLocaleString('ru-RU')}
              </div>
              <div style={{ fontSize: '0.6875rem', color: 'rgba(0,0,0,0.38)' }}>шт / мес</div>
            </div>
            <div className="stat-card" style={{ padding: '14px 16px' }}>
              <div className="mono text-base font-semibold leading-none mb-1 truncate" style={{ color: '#1A73E8' }}>
                {(d.salesRub / 1000).toFixed(0)}K ₽
              </div>
              <div style={{ fontSize: '0.6875rem', color: 'rgba(0,0,0,0.38)' }}>₽ / мес</div>
            </div>
            <div className="stat-card" style={{ padding: '14px 16px' }}>
              <div
                className="flex items-center gap-1 mono text-base font-semibold leading-none mb-1"
                style={{ color: d.shareChange >= 0 ? '#3B82F6' : '#8A8986' }}
              >
                {d.shareChange >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                {Math.abs(d.shareChange)}%
              </div>
              <div style={{ fontSize: '0.6875rem', color: 'rgba(0,0,0,0.38)' }}>доля рынка</div>
            </div>
          </div>
        </div>

        {/* Ассортимент */}
        <div className="mb-5">
          <p className="label mb-3">Другие товары продавца</p>
          <div className="rounded-xl overflow-hidden" style={{ border: '1px solid rgba(26,115,232,0.1)' }}>
            {d.products.map((p, i) => (
              <div
                key={i}
                className="flex items-center justify-between px-4 py-2.5"
                style={{ borderBottom: i < d.products.length - 1 ? '1px solid rgba(0,0,0,0.06)' : 'none' }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <div
                    className="w-5 h-5 rounded-md flex items-center justify-center shrink-0"
                    style={{ background: 'rgba(26,115,232,0.07)', border: '1px solid rgba(26,115,232,0.12)' }}
                  >
                    <Package size={9} style={{ color: '#1A73E8' }} />
                  </div>
                  <span className="text-xs truncate" style={{ color: '#202124' }}>{p.name}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0 ml-2">
                  <span className="mono text-xs font-semibold" style={{ color: '#202124' }}>
                    {p.price.toLocaleString('ru-RU')} ₽
                  </span>
                  <span className="flex items-center gap-0.5 text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
                    <Star size={9} style={{ color: '#F5A623', fill: '#F5A623' }} />
                    {p.rating.toFixed(1)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Стратегические выводы */}
        <div className="mb-6">
          <p className="label mb-3">Стратегические выводы</p>
          <div className="flex flex-wrap gap-2">
            {d.conclusions.map((label, i) => (
              <span
                key={i}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold"
                style={{ background: 'rgba(26,115,232,0.08)', color: '#1A73E8', border: '1px solid rgba(26,115,232,0.22)' }}
              >
                <AlertTriangle size={10} />
                {label}
              </span>
            ))}
          </div>
        </div>

        <button onClick={onClose} className="btn btn-ghost w-full">
          Закрыть досье
        </button>
      </div>
    </div>
  )
}

export function CompetitorCard({ competitor: c }: { competitor: Competitor }) {
  const [showDossier, setShowDossier] = useState(false)
  const sig = SIG[c.significance] ?? SIG.minor

  return (
    <>
      <div
        className="card card-hover p-5 group cursor-pointer"
        onClick={() => setShowDossier(true)}
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={sig.badge}>{sig.label}</span>
            <span className="text-[11px] font-mono" style={{ color: 'rgba(0,0,0,0.38)' }}>#{c.rank}</span>
          </div>
          {c.competitor_url ? (
            <a
              href={c.competitor_url}
              target="_blank"
              rel="noopener noreferrer"
              className="w-7 h-7 flex items-center justify-center rounded-lg transition-all duration-200 opacity-60 sm:opacity-0 sm:group-hover:opacity-100"
              style={{ color: '#8A8986', border: '1px solid transparent' }}
              onClick={e => e.stopPropagation()}
              onMouseEnter={e => {
                const el = e.currentTarget as HTMLElement
                el.style.color = '#3B82F6'; el.style.borderColor = 'rgba(26,115,232,0.18)'; el.style.background = 'rgba(26,115,232,0.06)'
              }}
              onMouseLeave={e => {
                const el = e.currentTarget as HTMLElement
                el.style.color = '#8A8986'; el.style.borderColor = 'transparent'; el.style.background = 'transparent'
              }}
            >
              <ArrowUpRight size={13} />
            </a>
          ) : (
            <div className="w-7 h-7" />
          )}
        </div>

        <h3 className="font-semibold text-sm mb-0.5 leading-snug" style={{ color: '#202124' }}>
          {c.competitor_name}
        </h3>
        <p className="text-[11px] mb-4" style={{ color: 'rgba(0,0,0,0.38)' }}>
          {MP[c.marketplace] ?? c.marketplace}
        </p>

        <div className="flex items-end justify-between">
          <div>
            <span className="label mb-0.5">Цена</span>
            <span className="block text-[17px] font-bold leading-none" style={{ color: '#202124' }}>
              {c.price.toLocaleString('ru-RU')} ₽
            </span>
          </div>
          <div className="flex items-center gap-3 text-[11px]" style={{ color: '#8A8986' }}>
            {c.rating !== null && (
              <span className="flex items-center gap-1">
                <Star size={10} style={{ color: '#1A73E8', fill: '#3B82F6' }} />
                {c.rating.toFixed(1)}
              </span>
            )}
            {c.reviews_count !== null && (
              <span className="flex items-center gap-1">
                <Users size={10} />
                {c.reviews_count.toLocaleString('ru-RU')}
              </span>
            )}
            {c.sales_estimate !== null && (
              <span className="flex items-center gap-1">
                <ShoppingCart size={10} />
                {c.sales_estimate.toLocaleString('ru-RU')}
              </span>
            )}
          </div>
        </div>

        <p className="text-[10px] mt-3 opacity-0 group-hover:opacity-100 transition-opacity" style={{ color: '#1A73E8' }}>
          Нажмите — открыть досье →
        </p>
      </div>

      {showDossier && (
        <DossierModal competitor={c} onClose={() => setShowDossier(false)} />
      )}
    </>
  )
}

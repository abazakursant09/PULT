'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { TrendingUp, TrendingDown, ShoppingCart, Zap } from 'lucide-react'

const MOCK_SPARKLINE = [420, 380, 510, 490, 620, 580, 710, 690, 750, 812]

function Sparkline({ data, color = '#7C3AED' }: { data: number[]; color?: string }) {
  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1
  const w = 120; const h = 40
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = h - ((v - min) / range) * h
    return `${x},${y}`
  }).join(' ')
  return (
    <svg width={w} height={h} style={{ overflow: 'visible', display: 'block' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Delta({ v, unit = '%' }: { v: number; unit?: string }) {
  const pos = v >= 0
  return (
    <span className="inline-flex items-center gap-0.5 text-[12px] font-semibold" style={{ color: pos ? '#22C55E' : '#EF4444' }}>
      {pos ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {pos ? '+' : ''}{v.toFixed(1)}{unit}
    </span>
  )
}

function Metric({ label, children, className = '', href, delay = 0, style: extraStyle }: {
  label: string; children: React.ReactNode; className?: string; href?: string; delay?: number; style?: React.CSSProperties
}) {
  const inner = (
    <div
      className={`card-bento h-full p-6 animate-fade-in ${className}`}
      style={{ animationDelay: `${delay}ms`, animationFillMode: 'both', ...extraStyle }}
    >
      {children}
    </div>
  )
  if (href) return (
    <Link href={href} style={{ textDecoration: 'none', display: 'block', height: '100%' }}>
      {inner}
    </Link>
  )
  return inner
}

function useGreeting(name?: string) {
  const h = new Date().getHours()
  const part = h < 12 ? 'утро' : h < 18 ? 'день' : 'вечер'
  const date = new Date().toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })
  return { greeting: `Доброе ${part}${name ? `, ${name}` : ''}`, date }
}

export function DashboardBento() {
  const [userName, setUserName] = useState<string | undefined>()
  const { greeting, date } = useGreeting(userName)

  useEffect(() => {
    const s = localStorage.getItem('user')
    if (s) try { setUserName(JSON.parse(s).name?.split(' ')[0]) } catch {}
  }, [])

  const REVENUE  = 4_520_000
  const ORDERS   = 1_847
  const MARGIN   = 18.0
  const AD_SPEND = 452_000

  return (
    <section className="p-8">
      {/* Greeting */}
      <div className="mb-8">
        <h1 className="text-[24px] font-bold leading-none" style={{ color: '#FFFFFF' }}>{greeting}</h1>
        <p className="mt-1 text-[13px] capitalize" style={{ color: '#71717A' }}>{date}</p>
      </div>

      {/* Bento grid — 3 cols */}
      <div className="grid grid-cols-3 gap-4">

        {/* ВЫРУЧКА — col-span-2, tall */}
        <Metric label="ВЫРУЧКА" href="/dashboard/finance" delay={0}
          className="col-span-2 flex flex-col justify-between" style={{ minHeight: 180 }}>
          <div>
            <p className="label mb-3">ВЫРУЧКА</p>
            <p className="font-bold leading-none mono" style={{ fontSize: 40, color: '#FFFFFF' }}>
              {(REVENUE / 1_000_000).toFixed(2)} <span style={{ color: '#7C3AED' }}>млн ₽</span>
            </p>
            <div className="mt-2 flex items-center gap-3">
              <Delta v={14.2} />
              <span className="text-[12px]" style={{ color: '#6B6B72' }}>vs прошлый месяц</span>
            </div>
          </div>
          <div className="mt-4">
            <Sparkline data={MOCK_SPARKLINE} color="#7C3AED" />
          </div>
        </Metric>

        {/* ЗАКАЗЫ */}
        <Metric label="ЗАКАЗЫ" href="/dashboard" delay={60}>
          <p className="label mb-3">ЗАКАЗЫ</p>
          <p className="font-bold text-[32px] leading-none mono" style={{ color: '#FFFFFF' }}>
            {ORDERS.toLocaleString('ru-RU')}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <Delta v={9.3} />
          </div>
          <div className="mt-4 flex items-center gap-2" style={{ color: '#6B6B72' }}>
            <ShoppingCart size={13} />
            <span className="text-[12px]">за этот месяц</span>
          </div>
        </Metric>

        {/* МАРЖА */}
        <Metric label="МАРЖА" href="/profit-calculator" delay={120}>
          <p className="label mb-3">МАРЖА</p>
          <p className="font-bold text-[32px] leading-none mono" style={{ color: MARGIN >= 15 ? '#22C55E' : '#EF4444' }}>
            {MARGIN.toFixed(1)}%
          </p>
          <div className="mt-3 h-1" style={{ background: 'rgba(255,255,255,0.08)', borderRadius: 1 }}>
            <div className="h-full" style={{ width: `${Math.min(MARGIN * 5, 100)}%`, background: MARGIN >= 15 ? '#22C55E' : '#EF4444', borderRadius: 1 }} />
          </div>
          <p className="mt-1 text-[11px]" style={{ color: '#6B6B72' }}>Цель 20%</p>
          <div className="mt-2">
            <Delta v={-1.4} unit=" п.п." />
          </div>
        </Metric>

        {/* РЕКЛАМА — col-span-2 */}
        <Metric label="РЕКЛАМА" href="/auto-promotions" delay={180} className="col-span-2">
          <div className="flex items-center justify-between">
            <div>
              <p className="label mb-3">РЕКЛАМА</p>
              <p className="font-bold text-[32px] leading-none mono" style={{ color: '#FFFFFF' }}>
                {(AD_SPEND / 1000).toFixed(0)} <span style={{ color: '#71717A', fontSize: 16 }}>тыс ₽</span>
              </p>
              <p className="mt-1 text-[12px]" style={{ color: '#6B6B72' }}>
                {((AD_SPEND / REVENUE) * 100).toFixed(1)}% от выручки
              </p>
              <div className="mt-2">
                <Delta v={-22.5} />
              </div>
            </div>
            <Zap size={32} style={{ color: 'rgba(110,106,252,0.18)', flexShrink: 0 }} />
          </div>
        </Metric>

      </div>
    </section>
  )
}

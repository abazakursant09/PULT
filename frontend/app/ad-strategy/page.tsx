'use client'

import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/AppShell'
import { ArrowLeft, Megaphone, TrendingUp, Target, BarChart2 } from 'lucide-react'

const STRATEGIES = [
  {
    title: 'Поисковая реклама',
    desc: 'Продвижение в поиске маркетплейса по ключевым запросам. Подходит для сезонных товаров и новинок.',
    icon: Target,
    color: '#7C3AED',
  },
  {
    title: 'Баннерная реклама',
    desc: 'Размещение баннеров на главной странице и в категориях. Максимальный охват аудитории.',
    icon: Megaphone,
    color: '#22C55E',
  },
  {
    title: 'Акции и скидки',
    desc: 'Участие в акциях маркетплейса: распродажи, купоны, программы лояльности.',
    icon: TrendingUp,
    color: '#F59E0B',
  },
  {
    title: 'Аналитика рекламы',
    desc: 'Отслеживание CTR, конверсии, ДРР и ROI по каждой кампании.',
    icon: BarChart2,
    color: '#EC4899',
  },
]

export default function AdStrategyPage() {
  const router = useRouter()

  return (
    <AppShell>
      <div className="p-8">
        {/* Back button */}
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-[13px] mb-6"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#71717A', padding: 0 }}
          onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
          onMouseLeave={e => { e.currentTarget.style.color = '#71717A' }}
        >
          <ArrowLeft size={14} /> Назад
        </button>

        {/* Header */}
        <div className="mb-8">
          <p className="label mb-2">ИНСТРУМЕНТЫ</p>
          <h1 className="text-[22px] font-bold mb-1" style={{ color: '#FFFFFF' }}>Реклама</h1>
          <p className="text-[13px]" style={{ color: '#71717A' }}>
            Стратегии продвижения на маркетплейсах — Wildberries, Ozon, Яндекс Маркет
          </p>
        </div>

        {/* Strategy cards */}
        <div className="grid grid-cols-2 gap-4">
          {STRATEGIES.map(({ title, desc, icon: Icon, color }) => (
            <div
              key={title}
              className="p-6 rounded-[8px]"
              style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}
            >
              <div
                className="w-10 h-10 rounded-[8px] flex items-center justify-center mb-4"
                style={{ background: `${color}18` }}
              >
                <Icon size={20} style={{ color }} />
              </div>
              <p className="text-[15px] font-semibold mb-2" style={{ color: '#FFFFFF' }}>{title}</p>
              <p className="text-[13px] leading-relaxed" style={{ color: '#71717A' }}>{desc}</p>
            </div>
          ))}
        </div>

        {/* Placeholder notice */}
        <div
          className="mt-8 p-4 rounded-[8px] text-center"
          style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.20)' }}
        >
          <p className="text-[13px]" style={{ color: '#71717A' }}>
            Полная интеграция с рекламными кабинетами маркетплейсов — скоро
          </p>
        </div>
      </div>
    </AppShell>
  )
}

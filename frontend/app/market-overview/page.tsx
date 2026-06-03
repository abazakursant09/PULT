'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  TrendingUp, BarChart3, Lightbulb, Trophy, ArrowRight, Users, Star, X, ChevronDown,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Card } from '@/components/ui/card'

const A   = '#1A73E8'
const T   = '#0A2540'
const M   = '#425466'
const ABG = 'rgba(26,115,232,0.08)'
const ABR = 'rgba(26,115,232,0.22)'

// ── Stub data ─────────────────────────────────────────────────────────────────
type Saturation = 'very_high' | 'high' | 'medium' | 'low' | 'free'

interface Category {
  id: number
  name: string
  sellers: number
  growth: number       // % demand growth
  newSellers: number   // % new sellers entering
  saturation: Saturation
  trend: number[]      // last 6 months seller count
  icon: string
}

const CATEGORIES: Category[] = [
  { id:  1, name: 'Одежда',          sellers: 48200, growth: 18, newSellers:  5, saturation: 'very_high', icon: '👗', trend: [40000,42000,44000,46000,47000,48200] },
  { id:  2, name: 'Обувь',           sellers: 22400, growth: 12, newSellers:  7, saturation: 'high',      icon: '👟', trend: [18000,19000,20000,21000,22000,22400] },
  { id:  3, name: 'Электроника',     sellers: 35600, growth: 25, newSellers: 15, saturation: 'very_high', icon: '📱', trend: [28000,30000,31000,33000,34000,35600] },
  { id:  4, name: 'Косметика',       sellers: 18700, growth: 35, newSellers: 12, saturation: 'high',      icon: '💄', trend: [12000,14000,15000,16500,17500,18700] },
  { id:  5, name: 'Игрушки',         sellers:  8900, growth: 42, newSellers:  3, saturation: 'medium',    icon: '🧸', trend: [ 6000, 6500, 7000, 7500, 8000, 8900] },
  { id:  6, name: 'Спорт',           sellers:  6200, growth: 28, newSellers:  8, saturation: 'medium',    icon: '⚽', trend: [ 4500, 5000, 5300, 5700, 6000, 6200] },
  { id:  7, name: 'Товары для дома', sellers: 29400, growth: 15, newSellers:  6, saturation: 'high',      icon: '🏠', trend: [24000,25000,26000,27000,28000,29400] },
  { id:  8, name: 'Автотовары',      sellers: 11300, growth: 20, newSellers:  9, saturation: 'medium',    icon: '🚗', trend: [ 8500, 9000, 9500,10000,10800,11300] },
  { id:  9, name: 'Продукты',        sellers:  4200, growth: 55, newSellers:  4, saturation: 'low',       icon: '🛒', trend: [ 2000, 2500, 3000, 3400, 3900, 4200] },
  { id: 10, name: 'Книги',           sellers:  3800, growth: 10, newSellers:  2, saturation: 'low',       icon: '📚', trend: [ 3200, 3300, 3400, 3500, 3650, 3800] },
  { id: 11, name: 'Зоотовары',       sellers:  5600, growth: 48, newSellers:  5, saturation: 'medium',    icon: '🐾', trend: [ 3500, 3800, 4200, 4700, 5200, 5600] },
  { id: 12, name: 'Бытовая химия',   sellers:  7800, growth: 22, newSellers:  7, saturation: 'medium',    icon: '🧹', trend: [ 5800, 6200, 6700, 7100, 7500, 7800] },
  { id: 13, name: 'Ювелирка',        sellers: 15600, growth:  8, newSellers:  3, saturation: 'high',      icon: '💍', trend: [14000,14500,14800,15000,15300,15600] },
  { id: 14, name: 'Сад и дача',      sellers:  3200, growth: 62, newSellers:  3, saturation: 'low',       icon: '🌿', trend: [ 1800, 2000, 2300, 2700, 3000, 3200] },
  { id: 15, name: 'Канцтовары',      sellers:  5100, growth: 18, newSellers:  6, saturation: 'medium',    icon: '✏️', trend: [ 4000, 4200, 4400, 4700, 4900, 5100] },
  { id: 16, name: 'Музыка и звук',   sellers:  2700, growth: 70, newSellers:  2, saturation: 'free',      icon: '🎵', trend: [ 1200, 1500, 1800, 2100, 2400, 2700] },
  { id: 17, name: 'Медтовары',       sellers:  9400, growth: 38, newSellers:  8, saturation: 'medium',    icon: '💊', trend: [ 6500, 7000, 7500, 8000, 8800, 9400] },
  { id: 18, name: 'Освещение',       sellers:  4100, growth: 45, newSellers:  4, saturation: 'low',       icon: '💡', trend: [ 2500, 2800, 3100, 3500, 3800, 4100] },
]

const SAT_CONFIG: Record<Saturation, { label: string; bg: string; text: string; dot: string }> = {
  very_high: { label: 'Переизбыток',    bg: 'rgba(220,38,38,0.08)',  text: '#b91c1c', dot: '#DC2626' },
  high:      { label: 'Насыщен',        bg: 'rgba(217,119,6,0.08)',  text: '#b45309', dot: '#D97706' },
  medium:    { label: 'Средний',        bg: 'rgba(26,115,232,0.08)', text: '#1A73E8', dot: '#1A73E8' },
  low:       { label: 'Свободная ниша', bg: 'rgba(26,115,232,0.12)', text: '#1d4ed8', dot: '#2563EB' },
  free:      { label: '🔥 Растущая',    bg: 'rgba(34,197,94,0.1)',   text: '#15803d', dot: '#22c55e' },
}

const MONTHS = ['Дек', 'Янв', 'Фев', 'Мар', 'Апр', 'Май']

const RECOMMENDATIONS = [
  { cat: 'Музыка и звук',   growth: 70, new: 2,  text: 'Спрос вырос на 70%, новых продавцов всего 2% — отличное время для входа.' },
  { cat: 'Сад и дача',      growth: 62, new: 3,  text: 'Сезонный спрос ускоряется, конкуренция минимальна.' },
  { cat: 'Продукты',        growth: 55, new: 4,  text: 'Онлайн-продукты ещё не насыщены — лоу-конкуренция при высоком спросе.' },
  { cat: 'Освещение',       growth: 45, new: 4,  text: 'Энергосберегающие технологии — быстрорастущая ниша с низкой конкуренцией.' },
  { cat: 'Зоотовары',       growth: 48, new: 5,  text: 'Рынок домашних животных устойчиво растёт, войти ещё несложно.' },
]

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface SuccessStory {
  id:          string
  title:       string
  text:        string
  author_name?: string | null
  date?:       string        // localStorage-only field
  created_at?: string        // API field
}

// ── Spark-line component ──────────────────────────────────────────────────────
function SparkLine({ data }: { data: number[] }) {
  const min = Math.min(...data)
  const max = Math.max(...data)
  const w = 60; const h = 22
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = h - ((v - min) / (max - min || 1)) * (h - 2) - 1
    return `${x},${y}`
  }).join(' ')
  return (
    <svg width={w} height={h} style={{ overflow: 'visible' }}>
      <polyline fill="none" stroke={A} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" points={pts} />
    </svg>
  )
}

// ── Bar chart ─────────────────────────────────────────────────────────────────
function BarChart({ data }: { data: Category[] }) {
  const sorted = [...data].sort((a, b) => b.sellers - a.sellers).slice(0, 12)
  const max    = Math.max(...sorted.map(d => d.sellers))
  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ minWidth: 640, padding: '0 8px' }}>
        <div className="flex items-end gap-2" style={{ height: 160 }}>
          {sorted.map(cat => {
            const pct = (cat.sellers / max) * 100
            const cfg = SAT_CONFIG[cat.saturation]
            return (
              <div key={cat.id} className="flex flex-col items-center gap-1" style={{ flex: 1, minWidth: 40 }}>
                <span className="mono text-[9px] font-semibold text-center" style={{ color: 'rgba(0,0,0,0.38)' }}>
                  {(cat.sellers / 1000).toFixed(0)}K
                </span>
                <div
                  className="w-full rounded-t-lg transition-all duration-300"
                  style={{ height: `${Math.max(pct * 1.4, 4)}px`, background: cfg.dot, opacity: 0.8 }}
                />
              </div>
            )
          })}
        </div>
        <div className="flex gap-2 mt-2" style={{ borderTop: '1px solid rgba(26,115,232,0.1)', paddingTop: 6 }}>
          {sorted.map(cat => (
            <div key={cat.id} style={{ flex: 1, minWidth: 40 }}>
              <p className="text-center text-[9px] font-medium truncate" style={{ color: M }}>{cat.icon}</p>
              <p className="text-center text-[8px] truncate" style={{ color: 'rgba(0,0,0,0.38)' }}>{cat.name}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
function formatDate(s: SuccessStory): string {
  if (s.date) return s.date
  if (s.created_at) return new Date(s.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
  return ''
}

// ── Category detail panel ─────────────────────────────────────────────────────
function CategoryDetail({ cat, onClose }: { cat: Category; onClose: () => void }) {
  const cfg   = SAT_CONFIG[cat.saturation]
  const rec   = RECOMMENDATIONS.find(r => r.cat === cat.name)
  const min   = Math.min(...cat.trend)
  const max   = Math.max(...cat.trend)
  const W = 320; const H = 80
  const pts = cat.trend.map((v, i) => {
    const x = (i / (cat.trend.length - 1)) * W
    const y = H - ((v - min) / (max - min || 1)) * (H - 6) - 3
    return `${x},${y}`
  }).join(' ')
  const growthPct = ((cat.trend[5] - cat.trend[0]) / cat.trend[0] * 100).toFixed(1)

  return (
    <div
      className="mt-4 rounded-2xl p-5 animate-fade-in"
      style={{ background: '#F1F3F4', border: `1px solid ${cfg.dot}40`, boxShadow: `0 8px 32px rgba(0,0,0,0.35)` }}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <span style={{ fontSize: '2rem' }}>{cat.icon}</span>
          <div>
            <h3 className="font-bold text-lg leading-tight" style={{ color: T }}>{cat.name}</h3>
            <span
              className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
              style={{ background: cfg.bg, color: cfg.text }}
            >
              {cfg.label}
            </span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="w-8 h-8 flex items-center justify-center rounded-xl shrink-0"
          style={{ background: 'rgba(0,0,0,0.05)', border: '1px solid rgba(0,0,0,0.08)', color: M, cursor: 'pointer' }}
        >
          <X size={14} />
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        {[
          { label: 'Продавцов', value: `${(cat.sellers / 1000).toFixed(1)}K` },
          { label: 'Рост спроса', value: `+${cat.growth}%` },
          { label: 'Новых продавцов', value: `${cat.newSellers}%` },
          { label: 'Рост за 6 мес.', value: `+${growthPct}%` },
        ].map((s, i) => (
          <div key={i}
            className="rounded-xl p-3 text-center"
            style={{ background: 'rgba(26,115,232,0.04)', border: '1px solid rgba(26,115,232,0.1)' }}
          >
            <p className="mono font-bold text-sm" style={{ color: A }}>{s.value}</p>
            <p className="text-[11px] mt-0.5" style={{ color: 'rgba(255,255,255,0.35)' }}>{s.label}</p>
          </div>
        ))}
      </div>

      {/* Trend chart */}
      <div className="mb-4">
        <p className="text-xs font-semibold tracking-wider uppercase mb-3" style={{ color: 'rgba(0,0,0,0.38)' }}>
          Динамика продавцов — последние 6 месяцев
        </p>
        <div
          className="rounded-xl p-4"
          style={{ background: 'rgba(13,13,15,0.6)', border: '1px solid rgba(26,115,232,0.08)', overflowX: 'auto' }}
        >
          <svg width={W} height={H + 20} style={{ overflow: 'visible', minWidth: W }}>
            {/* Grid lines */}
            {[0,1,2,3].map(i => {
              const y = (H / 3) * i
              return <line key={i} x1={0} y1={y} x2={W} y2={y} stroke="rgba(0,0,0,0.04)" strokeWidth={1} />
            })}
            {/* Area fill */}
            <defs>
              <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={A} stopOpacity="0.15" />
                <stop offset="100%" stopColor={A} stopOpacity="0" />
              </linearGradient>
            </defs>
            <polygon
              points={`0,${H} ${pts} ${W},${H}`}
              fill="url(#areaGrad)"
            />
            <polyline fill="none" stroke={A} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" points={pts} />
            {/* Dots */}
            {cat.trend.map((v, i) => {
              const x = (i / (cat.trend.length - 1)) * W
              const y = H - ((v - min) / (max - min || 1)) * (H - 6) - 3
              return <circle key={i} cx={x} cy={y} r={3} fill={A} />
            })}
            {/* Month labels */}
            {MONTHS.map((m, i) => {
              const x = (i / (MONTHS.length - 1)) * W
              return (
                <text key={i} x={x} y={H + 16} textAnchor="middle"
                  style={{ fontSize: 10, fill: 'rgba(255,255,255,0.3)', fontFamily: 'inherit' }}>
                  {m}
                </text>
              )
            })}
          </svg>
        </div>
      </div>

      {/* Recommendation */}
      {rec ? (
        <div
          className="rounded-xl p-4 flex items-start gap-3"
          style={{ background: ABG, border: `1px solid ${ABR}` }}
        >
          <Lightbulb size={15} style={{ color: A, flexShrink: 0, marginTop: 2 }} />
          <div>
            <p className="text-xs font-bold uppercase tracking-wider mb-1" style={{ color: A }}>
              Рекомендация
            </p>
            <p className="text-sm leading-relaxed" style={{ color: M }}>{rec.text}</p>
          </div>
        </div>
      ) : (
        <div
          className="rounded-xl p-4 flex items-start gap-3"
          style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(0,0,0,0.08)' }}
        >
          <BarChart3 size={15} style={{ color: M, flexShrink: 0, marginTop: 2 }} />
          <p className="text-sm" style={{ color: M }}>
            Высокая конкуренция в нише. Рекомендуем дифференцироваться по ассортименту или выбрать смежную категорию.
          </p>
        </div>
      )}
    </div>
  )
}

export default function MarketOverviewPage() {
  const [stories,       setStories]       = useState<SuccessStory[]>([])
  const [storiesLoaded, setStoriesLoaded] = useState(false)
  const [filter,        setFilter]        = useState<Saturation | 'all'>('all')
  const [selectedCat,   setSelectedCat]   = useState<Category | null>(null)

  useEffect(() => {
    async function loadStories() {
      // Fetch from API
      let apiStories: SuccessStory[] = []
      try {
        const res = await fetch(`${API}/api/success-stories`)
        if (res.ok) apiStories = await res.json()
      } catch {}

      // Merge localStorage stories (user's own unpublished / fallback)
      let localStories: SuccessStory[] = []
      try {
        const raw = localStorage.getItem('bp_success_stories')
        if (raw) localStories = JSON.parse(raw)
      } catch {}

      // Deduplicate: API stories take priority, then local-only ones
      const apiIds = new Set(apiStories.map(s => s.id))
      const merged = [...apiStories, ...localStories.filter(s => !apiIds.has(s.id))]
      setStories(merged)
      setStoriesLoaded(true)
    }
    loadStories()
  }, [])

  const visible = filter === 'all' ? CATEGORIES : CATEGORIES.filter(c => c.saturation === filter)

  return (
    <AppShell>
      <div className="flex-1 px-6 py-8" style={{ background: '#F6F9FC' }}>
      <div className="max-w-5xl mx-auto">

        {/* Hero */}
        <div className="mb-12 animate-fade-in">
          <div className="flex items-center gap-3 mb-4">
            <div style={{ width: 44, height: 44, borderRadius: 14, background: 'rgba(26,115,232,0.1)', border: '1px solid rgba(26,115,232,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: '0 0 20px rgba(26,115,232,0.12)' }}>
              <BarChart3 size={20} style={{ color: A }} />
            </div>
            <div>
              <h1 className="font-bold" style={{ fontSize: 'clamp(1.5rem, 3vw, 2.25rem)', color: T, lineHeight: 1.1 }}>Обзор рынка</h1>
            </div>
          </div>
          <p style={{ color: M, fontSize: '1.0625rem', maxWidth: 580, lineHeight: 1.65 }}>
            Актуальная аналитика по категориям маркетплейсов: где высокая конкуренция, а где ещё есть место для нового продавца.
          </p>
          <div style={{ width: 48, height: 2, background: 'linear-gradient(90deg, #3B82F6, transparent)', marginTop: 16, borderRadius: 2 }} />
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-3 mb-6">
          {(['all', 'free', 'low', 'medium', 'high', 'very_high'] as const).map(f => {
            const cfg = f === 'all' ? null : SAT_CONFIG[f]
            const active = filter === f
            return (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all"
                style={{
                  background: active ? (cfg ? cfg.bg : ABG) : 'rgba(0,0,0,0.04)',
                  color:  active ? (cfg ? cfg.text : A) : '#9CA3AF',
                  border: active ? `1px solid ${cfg ? cfg.dot : A}50` : '1px solid rgba(0,0,0,0.06)',
                }}
              >
                {cfg && <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: cfg.dot }} />}
                {f === 'all' ? 'Все категории' : cfg!.label}
              </button>
            )
          })}
        </div>

        {/* Heat map */}
        <Card className="p-6 sm:p-8 mb-8">
          <h2 className="font-bold text-lg mb-6" style={{ color: T }}>Тепловая карта ниш</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {visible.map((cat, idx) => {
              const cfg = SAT_CONFIG[cat.saturation]
              const isSelected = selectedCat?.id === cat.id
              return (
                <button
                  key={cat.id}
                  onClick={() => setSelectedCat(c => c?.id === cat.id ? null : cat)}
                  className="rounded-2xl p-4 animate-slide-up text-left"
                  style={{
                    background: isSelected ? `${cfg.dot}18` : cfg.bg,
                    border: isSelected ? `2px solid ${cfg.dot}80` : `1px solid ${cfg.dot}30`,
                    transition: 'transform 0.2s, box-shadow 0.2s, border-color 0.2s',
                    animationDuration: '0.5s', animationFillMode: 'both',
                    animationDelay: `${idx * 0.04}s`,
                    cursor: 'pointer',
                  }}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)'; (e.currentTarget as HTMLElement).style.boxShadow = `0 8px 24px rgba(0,0,0,0.2)` }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = ''; (e.currentTarget as HTMLElement).style.boxShadow = '' }}
                >
                  <div className="flex items-start justify-between mb-2">
                    <span style={{ fontSize: '1.375rem' }}>{cat.icon}</span>
                    <div className="flex items-center gap-1">
                      <span
                        className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: `${cfg.dot}20`, color: cfg.text }}
                      >
                        {cfg.label}
                      </span>
                    </div>
                  </div>
                  <p className="font-semibold text-sm mb-1" style={{ color: T }}>{cat.name}</p>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="mono text-xs font-bold" style={{ color: T }}>{(cat.sellers / 1000).toFixed(1)}K</p>
                      <p style={{ fontSize: '0.625rem', color: 'rgba(0,0,0,0.38)' }}>продавцов</p>
                    </div>
                    <SparkLine data={cat.trend} />
                  </div>
                  {isSelected && (
                    <div className="flex items-center gap-1 mt-2" style={{ color: A }}>
                      <ChevronDown size={11} />
                      <span style={{ fontSize: '0.6rem', fontWeight: 600 }}>детализация</span>
                    </div>
                  )}
                </button>
              )
            })}
          </div>

          {/* Inline category detail */}
          {selectedCat && (
            <CategoryDetail cat={selectedCat} onClose={() => setSelectedCat(null)} />
          )}
        </Card>

        {/* Bar chart */}
        <Card className="p-6 sm:p-8 mb-8">
          <div className="flex items-center gap-2 mb-6">
            <BarChart3 size={18} style={{ color: A }} />
            <h2 className="font-bold text-lg" style={{ color: T }}>Распределение продавцов по категориям</h2>
          </div>
          <BarChart data={CATEGORIES} />
          <p className="text-xs mt-4 text-center" style={{ color: 'rgba(0,0,0,0.38)' }}>
            Топ-12 категорий по числу продавцов · данные за май 2026
          </p>
        </Card>

        {/* Dynamics table */}
        <Card className="overflow-hidden mb-8">
          <div className="p-6 pb-0">
            <div className="flex items-center gap-2 mb-4">
              <TrendingUp size={18} style={{ color: A }} />
              <h2 className="font-bold text-lg" style={{ color: T }}>Динамика конкуренции за 6 месяцев</h2>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" style={{ minWidth: 640 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(26,115,232,0.1)', background: 'rgba(26,115,232,0.03)' }}>
                  <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">Категория</th>
                  {MONTHS.map(m => (
                    <th key={m} className="px-3 py-3 text-right text-xs font-semibold uppercase tracking-wide text-muted-foreground">{m}</th>
                  ))}
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wide text-muted-foreground">Рост</th>
                  <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wide text-muted-foreground">Статус</th>
                </tr>
              </thead>
              <tbody>
                {CATEGORIES.slice(0, 12).map((cat, i) => {
                  const cfg = SAT_CONFIG[cat.saturation]
                  const growthPct = ((cat.trend[5] - cat.trend[0]) / cat.trend[0] * 100).toFixed(1)
                  return (
                    <tr
                      key={cat.id}
                      style={{ borderBottom: i < 11 ? '1px solid rgba(26,115,232,0.07)' : 'none' }}
                    >
                      <td className="px-5 py-3">
                        <span className="flex items-center gap-2 font-medium text-sm" style={{ color: T }}>
                          {cat.icon} {cat.name}
                        </span>
                      </td>
                      {cat.trend.map((v, j) => (
                        <td key={j} className="px-3 py-3 text-right mono text-xs" style={{ color: M }}>
                          {(v / 1000).toFixed(0)}K
                        </td>
                      ))}
                      <td className="px-5 py-3 text-right">
                        <span className="mono text-xs font-bold" style={{ color: parseFloat(growthPct) > 15 ? '#3B82F6' : '#9A9897' }}>
                          +{growthPct}%
                        </span>
                      </td>
                      <td className="px-5 py-3 text-right">
                        <span
                          className="text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
                          style={{ background: cfg.bg, color: cfg.text }}
                        >
                          {cfg.label}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Recommendations */}
        <section className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <Lightbulb size={20} style={{ color: A }} />
            <h2 className="font-bold text-lg" style={{ color: T }}>Рекомендации: куда выгодно входить сейчас</h2>
          </div>
          <div className="space-y-3">
            {RECOMMENDATIONS.map((r, i) => (
              <div
                key={i}
                className="rounded-2xl p-5 animate-slide-up"
                style={{
                  background: '#F1F3F4', border: '1px solid rgba(26,115,232,0.1)',
                  boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                  transition: 'transform 0.2s, box-shadow 0.2s, border-color 0.2s',
                  animationDuration: '0.55s', animationFillMode: 'both',
                  animationDelay: `${i * 0.09}s`,
                }}
                onMouseEnter={e => { const el = e.currentTarget as HTMLElement; el.style.transform = 'translateY(-2px)'; el.style.boxShadow = '0 12px 32px rgba(0,0,0,0.25)'; el.style.borderColor = 'rgba(26,115,232,0.22)' }}
                onMouseLeave={e => { const el = e.currentTarget as HTMLElement; el.style.transform = ''; el.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)'; el.style.borderColor = 'rgba(26,115,232,0.1)' }}
              >
                <div className="flex items-start gap-4">
                  <div
                    className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5"
                    style={{ background: ABG, border: `1px solid ${ABR}` }}
                  >
                    <TrendingUp size={15} style={{ color: A }} />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span className="font-bold text-sm" style={{ color: T }}>{r.cat}</span>
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full" style={{ background: 'rgba(26,115,232,0.1)', color: '#1A73E8' }}>
                        спрос +{r.growth}%
                      </span>
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full" style={{ background: ABG, color: A }}>
                        новых продавцов {r.new}%
                      </span>
                    </div>
                    <p className="text-sm" style={{ color: M }}>{r.text}</p>
                  </div>
                  <Link
                    href="/register"
                    className="btn shrink-0 hidden sm:flex"
                    style={{ padding: '8px 16px', fontSize: '0.8125rem', background: A, color: '#fff', borderRadius: 10, fontWeight: 600, border: 'none', gap: 4 }}
                  >
                    Войти <ArrowRight size={12} />
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Success stories */}
        <section className="mb-8">
          <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
            <div className="flex items-center gap-2">
              <Trophy size={20} style={{ color: A }} />
              <h2 className="font-bold text-lg" style={{ color: T }}>Истории успеха</h2>
              {stories.length > 0 && (
                <span
                  className="mono text-[10px] font-bold px-2 py-0.5 rounded-full"
                  style={{ background: ABG, color: A, border: `1px solid ${ABR}` }}
                >
                  {stories.length}
                </span>
              )}
            </div>
            <Link
              href="/login"
              className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-xl"
              style={{ background: ABG, color: A, border: `1px solid ${ABR}` }}
            >
              <Trophy size={11} /> Поделиться своей историей
            </Link>
          </div>

          {storiesLoaded && stories.length === 0 && (
            <div
              className="rounded-2xl p-8 text-center"
              style={{ background: '#F1F3F4', border: `1px solid rgba(26,115,232,0.1)` }}
            >
              <div
                className="w-12 h-12 rounded-2xl flex items-center justify-center mx-auto mb-3"
                style={{ background: ABG, border: `1px solid ${ABR}` }}
              >
                <Trophy size={20} style={{ color: A }} />
              </div>
              <p className="font-semibold text-sm mb-1" style={{ color: T }}>Историй пока нет</p>
              <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
                Войдите в Пульт и поделитесь своим результатом — первой историей станете вы
              </p>
            </div>
          )}

          {stories.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {stories.map(s => (
                <div
                  key={s.id}
                  className="rounded-2xl p-5"
                  style={{ background: '#F1F3F4', border: `1px solid ${ABR}`, boxShadow: '0 2px 8px rgba(26,115,232,0.08)' }}
                >
                  <div className="flex items-start gap-3 mb-3">
                    <div
                      className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5"
                      style={{ background: ABG, border: `1px solid ${ABR}` }}
                    >
                      <Star size={13} style={{ color: A, fill: A }} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-semibold text-sm leading-snug" style={{ color: T }}>{s.title}</p>
                      <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                        {(s.author_name) && (
                          <span className="text-[11px] font-medium" style={{ color: A }}>{s.author_name}</span>
                        )}
                        <span className="text-[11px]" style={{ color: 'rgba(0,0,0,0.38)' }}>{formatDate(s)}</span>
                      </div>
                    </div>
                  </div>
                  <p className="text-sm leading-relaxed" style={{ color: M }}>{s.text}</p>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* CTA */}
        <div
          className="rounded-2xl p-8 sm:p-12 text-center overflow-hidden"
          style={{ background: '#F1F3F4', border: `1px solid rgba(26,115,232,0.2)`, boxShadow: '0 8px 40px rgba(26,115,232,0.1), inset 0 1px 0 rgba(26,115,232,0.07)', position: 'relative' }}
        >
          <div style={{ position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', width: 280, height: 140, background: 'radial-gradient(ellipse at 50% 0%, rgba(26,115,232,0.12) 0%, transparent 70%)', pointerEvents: 'none' }} />
          <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: 'linear-gradient(90deg, transparent, rgba(26,115,232,0.5), transparent)', borderRadius: '12px 12px 0 0' }} />
          <h3 className="font-bold text-xl mb-2" style={{ color: T }}>Готов занять свободную нишу?</h3>
          <p className="mb-6" style={{ color: M }}>
            Зарегистрируйтесь и получите полный анализ конкурентов, финансовое планирование и юридическую поддержку.
          </p>
          <div className="flex items-center justify-center gap-3 flex-wrap">
            <Link href="/register" className="btn"
              style={{ padding: '13px 28px', fontSize: '1rem', background: A, color: '#fff', borderRadius: 12, fontWeight: 600, border: 'none', boxShadow: '0 4px 16px rgba(26,115,232,0.3)' }}>
              Начать бесплатно <ArrowRight size={16} />
            </Link>
            <Link href="/academy" className="btn btn-ghost" style={{ padding: '13px 28px', fontSize: '1rem' }}>
              Академия
            </Link>
          </div>
        </div>

      </div>
      </div>
    </AppShell>
  )
}

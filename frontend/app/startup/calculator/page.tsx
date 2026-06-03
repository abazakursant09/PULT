'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Calculator, ArrowRight, ChevronLeft, TrendingUp, BarChart2, Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'

const CATEGORIES = [
  { value: 'electronics', label: 'Электроника' },
  { value: 'clothing',    label: 'Одежда и обувь' },
  { value: 'food',        label: 'Продукты питания' },
  { value: 'cosmetics',   label: 'Косметика и уход' },
  { value: 'home',        label: 'Товары для дома' },
  { value: 'sports',      label: 'Спорт и отдых' },
  { value: 'toys',        label: 'Игрушки' },
  { value: 'auto',        label: 'Авто и мото' },
]

const MARKETPLACES = [
  { value: 'wb',   label: 'Wildberries' },
  { value: 'ozon', label: 'Ozon' },
  { value: 'ym',   label: 'Яндекс Маркет' },
]

interface BreakdownItem { label: string; amount: number; percent: number }

interface CalcResult {
  budget: number
  category: string
  marketplace: string
  commission_rate: number
  breakdown: BreakdownItem[]
  total_costs: number
  estimated_profit: number
  margin_percent: number
  recommendation: string
}

const COMMISSION: Record<string, number> = { wb: 15, ozon: 10, ym: 6 }
const MP_LABELS:  Record<string, string> = { wb: 'Wildberries', ozon: 'Ozon', ym: 'Яндекс Маркет' }
const CAT_LABELS: Record<string, string> = {
  electronics: 'Электроника', clothing: 'Одежда и обувь', food: 'Продукты питания',
  cosmetics: 'Косметика и уход', home: 'Товары для дома', sports: 'Спорт и отдых',
  toys: 'Игрушки', auto: 'Авто и мото',
}

function calculate(budget: number, category: string, marketplace: string): CalcResult {
  const commission_rate = COMMISSION[marketplace] ?? 15
  const purchase        = Math.round(budget * 0.50)
  const logistics       = Math.round(budget * 0.12)
  const packaging       = Math.round(budget * 0.05)
  const advertising     = Math.round(budget * 0.10)
  const other           = Math.round(budget * 0.05)
  const revenue         = purchase * 2
  const commissionAmt   = Math.round(revenue * commission_rate / 100)
  const total_costs     = purchase + logistics + packaging + advertising + commissionAmt + other
  const estimated_profit = revenue - total_costs
  const margin_percent  = Math.round((estimated_profit / revenue) * 100)

  const breakdown: BreakdownItem[] = [
    { label: 'Закупка товара',                    amount: purchase,      percent: Math.round(purchase      / budget * 100) },
    { label: 'Доставка и хранение',               amount: logistics,     percent: Math.round(logistics     / budget * 100) },
    { label: 'Реклама',                           amount: advertising,   percent: Math.round(advertising   / budget * 100) },
    { label: `Комиссия ${MP_LABELS[marketplace]}`, amount: commissionAmt, percent: Math.round(commissionAmt / budget * 100) },
    { label: 'Упаковка и маркировка',             amount: packaging,     percent: Math.round(packaging     / budget * 100) },
    { label: 'Прочие расходы',                    amount: other,         percent: Math.round(other         / budget * 100) },
  ]

  const recommendation =
    margin_percent > 20
      ? `При маржинальности ${margin_percent}% ваш проект выглядит перспективно. Сфокусируйтесь на качестве карточки товара и первых отзывах — это сильнее всего влияет на конверсию.`
      : margin_percent > 10
      ? `Маржинальность ${margin_percent}% — умеренный показатель. Попробуйте снизить закупочную цену или найти более выгодную логистику.`
      : `Маржинальность ${margin_percent}% невысокая. Пересмотрите закупочную цену или рассмотрите другую категорию с меньшей конкуренцией.`

  return {
    budget,
    category: CAT_LABELS[category] ?? category,
    marketplace: MP_LABELS[marketplace] ?? marketplace,
    commission_rate,
    breakdown,
    total_costs,
    estimated_profit,
    margin_percent,
    recommendation,
  }
}

function fmt(n: number) { return n.toLocaleString('ru-RU') }

export default function CalculatorPage() {
  const [category,    setCategory]    = useState('home')
  const [budget,      setBudget]      = useState('')
  const [marketplace, setMarketplace] = useState('wb')
  const [result,      setResult]      = useState<CalcResult | null>(null)
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const b = parseFloat(budget.replace(/\s/g, ''))
    if (!b || b <= 0) { setError('Введите корректный бюджет'); return }
    setError('')
    setLoading(true)
    setTimeout(() => {
      setResult(calculate(b, category, marketplace))
      setLoading(false)
    }, 600)
  }

  const profitable = result && result.estimated_profit > 0
  const marginGood  = result && result.margin_percent > 15
  const marginColor = !result ? '#8A8986' : marginGood ? '#3B82F6' : '#8A8986'

  return (
    <div style={{ background: '#F8F9FA', minHeight: '100vh', color: '#202124' }}>

      {/* Nav */}
      <nav
        className="fixed top-0 inset-x-0 z-50"
        style={{
          background: 'rgba(13,13,15,0.92)',
          backdropFilter: 'blur(24px)',
          borderBottom: '1px solid rgba(26,115,232,0.1)',
        }}
      >
        <div className="max-w-5xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-baseline gap-0">
            <span className="font-bold text-xl tracking-tight" style={{ color: '#202124' }}>Бизнес‑</span>
            <span className="font-bold text-xl tracking-tight text-gradient-gold">Пульт</span>
          </Link>
          <Link href="/register" className="btn btn-primary" style={{ padding: '8px 18px', fontSize: '0.82rem' }}>
            Начать бесплатно
          </Link>
        </div>
      </nav>

      <div className="max-w-4xl mx-auto px-6 pt-28 pb-24 animate-fade-in">

        {/* Back + Header */}
        <div className="mb-10">
          <Link href="/startup" className="inline-flex items-center gap-1 text-sm mb-6" style={{ color: 'rgba(0,0,0,0.38)' }}>
            <ChevronLeft size={14} /> Назад к шагам
          </Link>
          <div className="flex items-center gap-3 mb-2">
            <div
              className="w-10 h-10 rounded-2xl flex items-center justify-center shrink-0"
              style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.16)' }}
            >
              <Calculator size={18} style={{ color: '#1A73E8' }} />
            </div>
            <h1 className="font-bold text-2xl" style={{ color: '#202124' }}>Калькулятор стартовых затрат</h1>
          </div>
          <p className="text-sm leading-relaxed" style={{ color: 'rgba(0,0,0,0.38)', paddingLeft: 52 }}>
            Укажите категорию, бюджет и маркетплейс — получите примерную смету расходов
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">

          {/* Form */}
          <Card className="p-6">
            <form onSubmit={handleSubmit} className="flex flex-col gap-5">
              <div>
                <Label className="mb-2">Категория товара</Label>
                <select className="input" value={category} onChange={e => setCategory(e.target.value)}>
                  {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </div>

              <div>
                <Label className="mb-2">Стартовый бюджет, ₽</Label>
                <Input
                  type="number"
                  placeholder="50 000"
                  value={budget}
                  onChange={e => setBudget(e.target.value)}
                  min="1000"
                  step="1000"
                />
                <p className="mt-1.5 text-[11px]" style={{ color: 'rgba(0,0,0,0.38)' }}>
                  Рекомендуем не менее 20 000 ₽ для первого запуска
                </p>
              </div>

              <div>
                <Label className="mb-2">Маркетплейс</Label>
                <select className="input" value={marketplace} onChange={e => setMarketplace(e.target.value)}>
                  {MARKETPLACES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>

              {error && (
                <p
                  className="text-sm px-3 py-2.5 rounded-xl"
                  style={{ background: 'rgba(26,115,232,0.06)', color: '#1A73E8', border: '1px solid rgba(26,115,232,0.15)' }}
                >
                  {error}
                </p>
              )}

              <Button type="submit" loading={loading} className="mt-0.5" style={{ padding: '13px' }}>
                {!loading && <><Calculator size={15} /> Рассчитать смету</>}
              </Button>
            </form>
          </Card>

          {/* Result */}
          {result ? (
            <Card className="p-6 flex flex-col gap-4">
              {/* Header */}
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <BarChart2 size={15} style={{ color: '#1A73E8' }} />
                  <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Смета на {fmt(result.budget)} ₽</span>
                </div>
                <span
                  className="text-[11px] px-2.5 py-1 rounded-full"
                  style={{ background: 'rgba(26,115,232,0.06)', color: '#1A73E8', border: '1px solid rgba(26,115,232,0.12)' }}
                >
                  {result.marketplace} · {result.commission_rate}%
                </span>
              </div>

              {/* Breakdown bars */}
              <div className="flex flex-col gap-3">
                {result.breakdown.map((item, i) => (
                  <div key={i}>
                    <div className="flex items-center justify-between mb-1">
                      <span style={{ color: '#8A8986', fontSize: '0.81rem' }}>{item.label}</span>
                      <span style={{ color: '#202124', fontSize: '0.81rem', fontWeight: 600 }}>
                        {fmt(item.amount)} ₽{' '}
                        <span style={{ color: 'rgba(0,0,0,0.38)', fontWeight: 400 }}>({item.percent}%)</span>
                      </span>
                    </div>
                    <div className="h-1 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.08)' }}>
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{ width: `${Math.min(item.percent, 100)}%`, background: 'rgba(26,115,232,0.5)' }}
                      />
                    </div>
                  </div>
                ))}
              </div>

              <div className="h-px" style={{ background: 'rgba(26,115,232,0.08)' }} />

              {/* Totals */}
              <div className="grid grid-cols-2 gap-3">
                <div
                  className="rounded-xl p-3.5 text-center"
                  style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(26,115,232,0.1)' }}
                >
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Итого расходов</div>
                  <div className="mono font-bold" style={{ color: '#5F6368', fontSize: '1rem' }}>
                    {fmt(result.total_costs)} ₽
                  </div>
                </div>
                <div
                  className="rounded-xl p-3.5 text-center"
                  style={{
                    background: profitable ? 'rgba(26,115,232,0.06)' : 'rgba(255,255,255,0.03)',
                    border: `1px solid ${profitable ? 'rgba(26,115,232,0.2)' : 'rgba(0,0,0,0.08)'}`,
                  }}
                >
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Оценочная прибыль</div>
                  <div className="mono font-bold" style={{ color: profitable ? '#3B82F6' : '#8A8986', fontSize: '1rem' }}>
                    {profitable ? '+' : ''}{fmt(result.estimated_profit)} ₽
                  </div>
                </div>
              </div>

              <div
                className="rounded-xl p-3.5 text-center"
                style={{ background: 'rgba(26,115,232,0.04)', border: '1px solid rgba(26,115,232,0.1)' }}
              >
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Маржинальность</div>
                <div className="mono font-bold text-xl" style={{ color: marginColor }}>
                  {result.margin_percent}%
                </div>
                <div className="text-[11px] mt-1" style={{ color: 'rgba(0,0,0,0.38)' }}>
                  {marginGood ? 'Хороший показатель' : 'Стоит оптимизировать'}
                </div>
              </div>

              <p className="text-sm leading-relaxed" style={{ color: '#8A8986' }}>
                {result.recommendation}
              </p>

              <Link href="/register" className="btn btn-primary" style={{ padding: '12px', justifyContent: 'center' }}>
                Начать с Бизнес-Пультом <ArrowRight size={14} />
              </Link>
            </Card>
          ) : (
            <Card
              className="flex flex-col items-center justify-center text-center gap-4 p-10"
              style={{ minHeight: 420 }}
            >
              <div
                className="w-14 h-14 rounded-2xl flex items-center justify-center"
                style={{ background: 'rgba(26,115,232,0.07)', border: '1px solid rgba(26,115,232,0.14)' }}
              >
                <TrendingUp size={24} style={{ color: 'rgba(26,115,232,0.5)' }} />
              </div>
              <div>
                <p className="font-medium mb-1.5" style={{ color: '#202124' }}>Заполните форму</p>
                <p style={{ color: 'rgba(0,0,0,0.38)', fontSize: '0.85rem' }}>
                  Выберите категорию, укажите бюджет<br />и нажмите «Рассчитать»
                </p>
              </div>
            </Card>
          )}
        </div>

        {/* Bottom CTA */}
        <div className="mt-10 text-center">
          <p className="text-sm mb-4" style={{ color: 'rgba(0,0,0,0.38)' }}>Нужна более точная аналитика?</p>
          <Link href="/register" className="btn btn-primary" style={{ padding: '13px 32px', fontSize: '0.95rem' }}>
            Начать с Бизнес-Пультом <ArrowRight size={16} />
          </Link>
        </div>

      </div>
    </div>
  )
}

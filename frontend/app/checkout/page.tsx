'use client'

import { Suspense, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { CheckCircle2, CreditCard, Lock, Sparkles, RefreshCw, Tag, X, CheckCircle, Loader2 } from 'lucide-react'
import { api, PromoValidateResult } from '@/lib/api'
import { Label } from '@/components/ui/label'

const BG  = '#F8F9FA'
const CARD = 'rgba(22,22,28,0.9)'
const A   = '#3B82F6'
const T   = '#FFFFFF'
const M   = '#9CA3AF'
const ABG = 'rgba(26,115,232,0.08)'
const ABR = 'rgba(26,115,232,0.22)'

const inp: React.CSSProperties = {
  width: '100%', background: CARD, border: '1px solid rgba(26,115,232,0.15)',
  borderRadius: 12, padding: '13px 16px', fontSize: '1rem', color: T,
  outline: 'none', fontFamily: 'inherit',
}

const PLANS: Record<string, { name: string; price: string; period: string; features: string[] }> = {
  start: {
    name: 'Старт',
    price: '9 990',
    period: 'единоразово на 90 дней',
    features: [
      'Доступ ко всем модулям на 90 дней',
      'Конкурентная разведка',
      'Управление ценами',
      'Автоответы на отзывы',
      'Финансовая аналитика',
    ],
  },
  master: {
    name: 'Мастер',
    price: '6 990',
    period: 'в месяц, ежемесячная подписка',
    features: [
      'Все функции тарифа «Старт»',
      'Мониторинг конкурентов в реальном времени',
      'Юридический модуль',
      'Приоритетная поддержка',
      'Отмена в любой момент',
    ],
  },
  profi: {
    name: 'Профи',
    price: '24 990',
    period: 'в месяц, ежемесячная подписка',
    features: [
      'Все функции тарифа «Мастер»',
      'До 10 пользователей в аккаунте',
      'Персональный менеджер',
      'API-интеграции под запрос',
      'SLA 99,9% доступности',
    ],
  },
}

function CheckoutForm() {
  const router = useRouter()
  const params = useSearchParams()
  const planKey = params.get('plan') ?? 'start'
  const plan = PLANS[planKey] ?? PLANS.start

  const [loading,     setLoading]     = useState(false)
  const [paid,        setPaid]        = useState(false)
  const [promoCode,   setPromoCode]   = useState('')
  const [promoResult, setPromoResult] = useState<PromoValidateResult | null>(null)
  const [promoErr,    setPromoErr]    = useState('')
  const [promoLoading, setPromoLoading] = useState(false)

  // Map checkout plan keys to API plan keys
  const planMap: Record<string, string> = { start: 'master', master: 'profi', profi: 'maximum' }
  const apiPlan = planMap[planKey] ?? 'profi'

  async function applyPromo() {
    if (!promoCode.trim()) return
    setPromoLoading(true); setPromoErr('')
    try {
      const res = await api.promo.validate(promoCode.trim(), apiPlan)
      if (res.valid) {
        setPromoResult(res)
      } else {
        setPromoErr(res.error ?? 'Неверный промокод')
        setPromoResult(null)
      }
    } catch { setPromoErr('Ошибка проверки') }
    finally { setPromoLoading(false) }
  }

  function removePromo() { setPromoResult(null); setPromoCode(''); setPromoErr('') }

  // Compute final price
  const rawPrice = parseInt(plan.price.replace(/\s/g, ''), 10)
  let finalPrice = rawPrice
  if (promoResult?.type === 'percent')   finalPrice = Math.max(rawPrice - Math.round(rawPrice * (promoResult.value ?? 0) / 100), 0)
  if (promoResult?.type === 'fixed')     finalPrice = Math.max(rawPrice - (promoResult.value ?? 0), 0)
  if (promoResult?.type === 'blogger_free') finalPrice = 0
  const fmt = (n: number) => new Intl.NumberFormat('ru-RU').format(n)

  function handlePay() {
    setLoading(true)
    setTimeout(() => {
      if (typeof window !== 'undefined') {
        localStorage.setItem('activePlan', planKey)
      }
      setPaid(true)
      setLoading(false)
      setTimeout(() => router.push('/dashboard'), 2000)
    }, 1500)
  }

  if (paid) {
    return (
      <div style={{ background: BG, minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ background: CARD, borderRadius: 20, padding: 48, textAlign: 'center', maxWidth: 420, boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
          <div style={{ width: 64, height: 64, borderRadius: '50%', background: ABG, border: `2px solid ${ABR}`, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
            <CheckCircle2 size={28} style={{ color: A }} />
          </div>
          <h2 style={{ fontSize: '1.5rem', fontWeight: 700, color: T, marginBottom: 8 }}>Оплата прошла успешно!</h2>
          <p style={{ color: M }}>Переходим в панель управления...</p>
          <div style={{ marginTop: 20, display: 'flex', justifyContent: 'center' }}>
            <Loader2 size={20} className="animate-spin text-muted-foreground" />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ background: BG, minHeight: '100vh', color: T, position: 'relative', overflow: 'hidden' }}>
      <div aria-hidden style={{ position: 'fixed', top: '5%', right: '-6%', width: 380, height: 380, background: 'radial-gradient(circle, rgba(26,115,232,0.06) 0%, transparent 65%)', animation: 'orbDrift 18s ease-in-out infinite', filter: 'blur(44px)', pointerEvents: 'none', zIndex: 0 }} />

      {/* Nav */}
      <nav style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(13,13,15,0.97)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)', borderBottom: '1px solid rgba(26,115,232,0.1)', boxShadow: '0 1px 0 rgba(26,115,232,0.05)' }}>
        <div className="max-w-lg mx-auto px-6 h-14 flex items-center justify-between">
          <Link href="/" style={{ color: M, fontSize: '0.875rem', textDecoration: 'none' }}>← Назад</Link>
          <div style={{ display: 'flex', alignItems: 'baseline' }}>
            <span style={{ fontWeight: 700, color: T }}>Бизнес‑</span>
            <span style={{ fontWeight: 700, color: A }}>Пульт</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: M, fontSize: '0.8125rem' }}>
            <Lock size={11} />
            Безопасная оплата
          </div>
        </div>
      </nav>

      <div className="max-w-lg mx-auto px-6 py-14">
        <div style={{ background: CARD, borderRadius: 20, padding: 'clamp(28px,5vw,44px)', boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.08)' }}>

          {/* Header */}
          <div className="flex items-center gap-3 mb-7">
            <div style={{ width: 44, height: 44, borderRadius: 12, background: ABG, border: `1px solid ${ABR}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Sparkles size={20} style={{ color: A }} />
            </div>
            <div>
              <h1 style={{ fontSize: '1.25rem', fontWeight: 700, color: T }}>Тариф «{plan.name}»</h1>
              <p style={{ fontSize: '0.875rem', color: M }}>Оформление подписки</p>
            </div>
          </div>

          {/* Summary */}
          <div className="rounded-xl p-5 mb-5" style={{ background: ABG, border: `1px solid ${ABR}` }}>
            <div className="flex items-baseline justify-between mb-3">
              <span style={{ color: M, fontSize: '0.9375rem' }}>Тариф «{plan.name}»</span>
              <span style={{ fontWeight: 600, color: T }}>{plan.price} ₽</span>
            </div>
            {promoResult && (promoResult.type === 'percent' || promoResult.type === 'fixed') && (
              <div className="flex items-baseline justify-between mb-2" style={{ fontSize: '0.875rem' }}>
                <span style={{ color: '#1A73E8' }}>Скидка по промокоду</span>
                <span style={{ color: '#1A73E8', fontWeight: 600 }}>
                  −{promoResult.type === 'percent'
                    ? `${promoResult.value}% (${fmt(rawPrice - finalPrice)} ₽)`
                    : `${fmt(promoResult.value ?? 0)} ₽`}
                </span>
              </div>
            )}
            <div style={{ borderTop: '1px solid rgba(26,115,232,0.1)', paddingTop: 12, display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 600, color: T }}>Итого сейчас</span>
              <div className="text-right">
                {promoResult && finalPrice !== rawPrice && (
                  <p style={{ fontSize: '0.8rem', color: 'rgba(0,0,0,0.38)', textDecoration: 'line-through' }}>{plan.price} ₽</p>
                )}
                <span style={{ fontWeight: 700, fontSize: '1.25rem', color: promoResult ? '#3B82F6' : T }}>
                  {promoResult?.type === 'blogger_free' ? 'Бесплатно' : `${fmt(finalPrice)} ₽`}
                </span>
              </div>
            </div>
            {promoResult?.type === 'extended_trial' && (
              <p style={{ fontSize: '0.75rem', color: '#1A73E8', marginTop: 6 }}>
                ✓ Пробный период продлён до {promoResult.trial_days} дней
              </p>
            )}
            {promoResult?.type === 'blogger_free' && (
              <p style={{ fontSize: '0.75rem', color: '#1A73E8', marginTop: 6 }}>
                ✓ {promoResult.trial_days} дней бесплатного доступа
              </p>
            )}
            <p style={{ fontSize: '0.75rem', color: 'rgba(0,0,0,0.38)', marginTop: 8 }}>{plan.period}</p>
          </div>

          {/* Promo code */}
          <div className="mb-6">
            <Label className="mb-1.5 flex items-center gap-1.5">
              <Tag size={12} style={{ color: A }} /> Промокод
            </Label>
            {promoResult ? (
              <div className="flex items-center gap-3 rounded-xl px-4 py-3"
                style={{ background: 'rgba(26,115,232,0.06)', border: '1px solid rgba(26,115,232,0.3)' }}>
                <CheckCircle size={16} style={{ color: '#1A73E8', flexShrink: 0 }} />
                <div className="flex-1 min-w-0">
                  <span className="font-mono font-semibold text-sm" style={{ color: '#1A73E8' }}>{promoResult.code}</span>
                  {promoResult.description && (
                    <p className="text-xs mt-0.5" style={{ color: '#8A8986' }}>{promoResult.description}</p>
                  )}
                </div>
                <button onClick={removePromo} style={{ color: '#1A73E8' }}>
                  <X size={15} />
                </button>
              </div>
            ) : (
              <div>
                <div className="flex gap-2">
                  <input
                    style={{ ...inp, fontFamily: 'monospace', textTransform: 'uppercase', flex: 1 }}
                    placeholder="ВВЕДИТЕ ПРОМОКОД"
                    value={promoCode}
                    onChange={e => { setPromoCode(e.target.value.toUpperCase()); setPromoErr('') }}
                    onKeyDown={e => e.key === 'Enter' && applyPromo()}
                  />
                  <button onClick={applyPromo} disabled={!promoCode.trim() || promoLoading}
                    style={{ padding: '0 18px', background: A, color: '#fff', borderRadius: 12, fontWeight: 600, fontSize: '0.875rem', border: 'none', cursor: 'pointer', whiteSpace: 'nowrap', opacity: (!promoCode.trim() || promoLoading) ? 0.5 : 1 }}>
                    {promoLoading ? '...' : 'Применить'}
                  </button>
                </div>
                {promoErr && <p style={{ color: '#dc2626', fontSize: '0.75rem', marginTop: 6 }}>{promoErr}</p>}
              </div>
            )}
          </div>

          {/* Included */}
          <div className="mb-7 space-y-2.5">
            {plan.features.map(f => (
              <div key={f} className="flex items-center gap-2">
                <CheckCircle2 size={13} style={{ color: A, flexShrink: 0 }} />
                <span style={{ fontSize: '0.875rem', color: M }}>{f}</span>
              </div>
            ))}
          </div>

          {/* Form stub */}
          <div className="space-y-4 mb-6">
            <div>
              <Label className="mb-1.5">Номер карты</Label>
              <input style={inp} placeholder="0000 0000 0000 0000" readOnly />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="mb-1.5">Срок действия</Label>
                <input style={inp} placeholder="MM / YY" readOnly />
              </div>
              <div>
                <Label className="mb-1.5">CVV</Label>
                <input style={inp} placeholder="•••" type="password" readOnly />
              </div>
            </div>
          </div>

          <button onClick={handlePay} disabled={loading}
            style={{ width: '100%', paddingTop: 15, paddingBottom: 15, fontSize: '1rem', borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: loading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, boxShadow: '0 4px 16px rgba(26,115,232,0.3)', opacity: loading ? 0.75 : 1 }}>
            {loading
              ? <><RefreshCw size={15} className="animate-spin" /> Обрабатываем...</>
              : promoResult?.type === 'blogger_free'
              ? <><Sparkles size={15} /> Активировать бесплатно</>
              : <><CreditCard size={15} /> Оплатить {fmt(finalPrice)} ₽</>}
          </button>

          <p className="text-center mt-4" style={{ fontSize: '0.75rem', color: 'rgba(0,0,0,0.38)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
            <Lock size={10} />
            Защищённое соединение · Отмена в один клик
          </p>
        </div>

        <p className="text-center mt-6" style={{ fontSize: '0.8125rem', color: 'rgba(0,0,0,0.38)' }}>
          Оплата через ЮKassa · Данные карты не хранятся
        </p>
      </div>
    </div>
  )
}

export default function CheckoutPage() {
  return (
    <Suspense fallback={<div style={{ background: '#F8F9FA', minHeight: '100vh', color: '#202124' }} />}>
      <CheckoutForm />
    </Suspense>
  )
}

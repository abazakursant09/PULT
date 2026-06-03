'use client'
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { CreditCard, Zap, Crown, Check, Clock, AlertCircle, Loader2 } from 'lucide-react'
import { api, type User, type Payment } from '@/lib/api'

const PLAN_LABELS: Record<string, string> = {
  master:  'Старт',
  profi:   'Мастер',
  maximum: 'Профи',
}

const TARIFFS = [
  {
    id: 'basic' as const,
    name: 'Старт',
    price: 990,
    plan: 'master',
    icon: Zap,
    features: ['До 5 товаров', 'Мониторинг цен', 'Анализ конкурентов', 'ИИ-ответы на отзывы'],
  },
  {
    id: 'pro' as const,
    name: 'Мастер',
    price: 4990,
    plan: 'profi',
    icon: Crown,
    features: ['До 50 товаров', 'Всё из Старта', 'Финансовый модуль', 'Приоритетная поддержка', 'Telegram-уведомления'],
    popular: true,
  },
]

const STATUS_LABELS: Record<string, string> = {
  pending:   'Ожидает',
  succeeded: 'Оплачен',
  canceled:  'Отменён',
}

const STATUS_COLORS: Record<string, string> = {
  pending:   '#F59E0B',
  succeeded: '#10B981',
  canceled:  '#EF4444',
}

function fmt(iso: string) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
}

export default function BillingPage() {
  const router = useRouter()
  const [user, setUser] = useState<User | null>(null)
  const [history, setHistory] = useState<Payment[]>([])
  const [loading, setLoading] = useState(true)
  const [paying, setPaying] = useState<'basic' | 'pro' | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const stored = localStorage.getItem('user')
      if (!stored) { router.push('/login'); return }
      const u: User = JSON.parse(stored)
      setUser(u)
      const h = await api.payments.history()
      setHistory(h)
    } catch {
      setError('Не удалось загрузить данные')
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => { load() }, [load])

  async function handlePay(tariff: 'basic' | 'pro') {
    setError('')
    setPaying(tariff)
    try {
      const res = await api.payments.create(tariff)
      window.location.href = res.confirmation_url
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка при создании платежа')
      setPaying(null)
    }
  }

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', background: '#0A0A0A', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Loader2 size={32} style={{ color: '#7C3AED', animation: 'spin 1s linear infinite' }} />
      </div>
    )
  }

  const currentPlanLabel = user ? (PLAN_LABELS[user.plan] ?? user.plan) : '—'

  return (
    <div style={{ minHeight: '100vh', background: '#0A0A0A', fontFamily: 'Inter, Arial, sans-serif' }}>
      <div style={{ maxWidth: 860, margin: '0 auto', padding: '40px 20px 80px' }}>

        {/* Header */}
        <div style={{ marginBottom: 36 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <CreditCard size={16} color="#7C3AED" />
            </div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: '#FFFFFF', margin: 0 }}>Подписка и оплата</h1>
          </div>
          <p style={{ fontSize: 14, color: '#A0A0A0', margin: 0 }}>Управление тарифом и история платежей</p>
        </div>

        {error && (
          <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)', borderRadius: 10, padding: '12px 16px', marginBottom: 24, display: 'flex', alignItems: 'center', gap: 10 }}>
            <AlertCircle size={16} color="#EF4444" />
            <span style={{ fontSize: 14, color: '#EF4444' }}>{error}</span>
          </div>
        )}

        {/* Current plan */}
        <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: '24px 28px', marginBottom: 28 }}>
          <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', color: '#A0A0A0', textTransform: 'uppercase', marginBottom: 12 }}>Текущий тариф</p>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <span style={{ fontSize: 26, fontWeight: 700, color: '#FFFFFF' }}>{currentPlanLabel}</span>
                <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.06em', color: '#7C3AED', background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.25)', borderRadius: 6, padding: '2px 8px' }}>
                  АКТИВЕН
                </span>
              </div>
              {user?.subscription_end_date && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Clock size={13} color="#A0A0A0" />
                  <span style={{ fontSize: 13, color: '#A0A0A0' }}>До {fmt(user.subscription_end_date)}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Tariff cards */}
        <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', color: '#A0A0A0', textTransform: 'uppercase', marginBottom: 16 }}>Выбрать тариф</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginBottom: 40 }}>
          {TARIFFS.map(t => {
            const Icon = t.icon
            const isCurrent = user?.plan === t.plan
            const isLoading = paying === t.id
            return (
              <div key={t.id} style={{ background: '#111113', border: t.popular ? '1px solid rgba(110,106,252,0.35)' : '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: '24px', position: 'relative', display: 'flex', flexDirection: 'column' }}>
                {t.popular && (
                  <div style={{ position: 'absolute', top: -1, right: 20, background: '#7C3AED', fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', color: '#FFFFFF', padding: '4px 12px', borderRadius: '0 0 8px 8px' }}>
                    ПОПУЛЯРНЫЙ
                  </div>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <div style={{ width: 36, height: 36, borderRadius: 10, background: t.popular ? 'rgba(110,106,252,0.12)' : 'rgba(255,255,255,0.05)', border: t.popular ? '1px solid rgba(110,106,252,0.25)' : '1px solid rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Icon size={16} color={t.popular ? '#7C3AED' : '#A0A0A0'} />
                  </div>
                  <span style={{ fontSize: 16, fontWeight: 700, color: '#FFFFFF' }}>{t.name}</span>
                </div>
                <div style={{ marginBottom: 20 }}>
                  <span style={{ fontSize: 32, fontWeight: 700, color: '#FFFFFF' }}>{t.price.toLocaleString('ru-RU')} ₽</span>
                  <span style={{ fontSize: 14, color: '#A0A0A0' }}> / мес</span>
                </div>
                <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 24px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {t.features.map(f => (
                    <li key={f} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Check size={14} color="#10B981" />
                      <span style={{ fontSize: 13, color: '#D0D0D0' }}>{f}</span>
                    </li>
                  ))}
                </ul>
                <div style={{ marginTop: 'auto' }}>
                  {isCurrent ? (
                    <div style={{ textAlign: 'center', fontSize: 13, fontWeight: 600, color: '#7C3AED', padding: '12px', background: 'rgba(110,106,252,0.08)', borderRadius: 10, border: '1px solid rgba(110,106,252,0.18)' }}>
                      Текущий тариф
                    </div>
                  ) : (
                    <button
                      onClick={() => handlePay(t.id)}
                      disabled={isLoading || paying !== null}
                      style={{ width: '100%', padding: '13px', background: t.popular ? '#7C3AED' : 'rgba(255,255,255,0.07)', color: '#FFFFFF', fontWeight: 700, fontSize: 14, border: 'none', borderRadius: 10, cursor: paying !== null ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: paying !== null && !isLoading ? 0.5 : 1, transition: 'opacity 0.2s' }}
                    >
                      {isLoading ? <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> : null}
                      {isLoading ? 'Переход к оплате…' : 'Подключить'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* Payment history */}
        <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, overflow: 'hidden' }}>
          <div style={{ padding: '20px 24px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', color: '#A0A0A0', textTransform: 'uppercase', margin: 0 }}>История платежей</p>
          </div>
          {history.length === 0 ? (
            <div style={{ padding: '40px 24px', textAlign: 'center' }}>
              <p style={{ fontSize: 14, color: '#A0A0A0', margin: 0 }}>Платежей пока нет</p>
            </div>
          ) : (
            <div>
              {history.map((p, i) => (
                <div key={p.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 24px', borderBottom: i < history.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none', flexWrap: 'wrap', gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: '#FFFFFF', marginBottom: 2 }}>
                      Тариф {PLAN_LABELS[p.plan] ?? p.plan}
                    </div>
                    <div style={{ fontSize: 12, color: '#A0A0A0' }}>{fmt(p.created_at)}</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: '#FFFFFF' }}>
                      {Number(p.amount).toLocaleString('ru-RU')} ₽
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: STATUS_COLORS[p.status] ?? '#A0A0A0', background: `${STATUS_COLORS[p.status] ?? '#A0A0A0'}14`, borderRadius: 6, padding: '3px 10px', border: `1px solid ${STATUS_COLORS[p.status] ?? '#A0A0A0'}30` }}>
                      {STATUS_LABELS[p.status] ?? p.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

      </div>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

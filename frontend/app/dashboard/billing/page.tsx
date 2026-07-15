'use client'
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { CreditCard, Clock, AlertCircle, Loader2 } from 'lucide-react'
import { api, type User, type Payment } from '@/lib/api'
import { PLAN_LABELS, hasActiveSubscription, planLabel } from '@/lib/plans'

// L0.1 — honest FREE Advisory-MVP billing surface. The tariff cards + "Подключить" buy button
// (which reached a real YooKassa charge for modules the MVP does not ship) are removed. This page
// now states plainly that paid plans are not available yet, and shows only real, honest data:
// a current-plan block that appears solely for a seller whose plan a payment actually activated,
// and the real payment history. No selling, no fake status, no claims about unshipped modules.
// The api.payments backend is untouched and simply no longer called from here.

const STATUS_LABELS: Record<string, string> = {
  pending:   'Ожидает',
  succeeded: 'Оплачен',
  canceled:  'Отменён',
}
const STATUS_COLORS: Record<string, string> = {
  pending:   'var(--warning)',
  succeeded: 'var(--success)',
  canceled:  'var(--danger)',
}

function fmt(iso: string) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
}

export default function BillingPage() {
  const router = useRouter()
  const [user, setUser] = useState<User | null>(null)
  const [history, setHistory] = useState<Payment[]>([])
  const [loading, setLoading] = useState(true)
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

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Loader2 size={32} style={{ color: 'var(--violet)', animation: 'spin 1s linear infinite' }} />
      </div>
    )
  }

  // `plan` defaults to 'master' at registration, so it cannot say whether anything was bought.
  // Only a subscription_end_date — written by the backend when a payment activates a plan — can.
  const paid = hasActiveSubscription(user)

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', fontFamily: 'Inter, Arial, sans-serif' }}>
      <div style={{ maxWidth: 860, margin: '0 auto', padding: '40px 20px 80px' }}>

        {/* Header */}
        <div style={{ marginBottom: 36 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: 'rgba(124,58,237,0.12)', border: '1px solid rgba(124,58,237,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <CreditCard size={16} color="var(--violet)" />
            </div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)', margin: 0 }}>Подписка и оплата</h1>
          </div>
          <p style={{ fontSize: 14, color: 'var(--text-2)', margin: 0 }}>История платежей</p>
        </div>

        {error && (
          <div style={{ background: 'var(--danger-dim)', border: '1px solid var(--danger)', borderRadius: 10, padding: '12px 16px', marginBottom: 24, display: 'flex', alignItems: 'center', gap: 10 }}>
            <AlertCircle size={16} color="var(--danger)" />
            <span style={{ fontSize: 14, color: 'var(--danger)' }}>{error}</span>
          </div>
        )}

        {/* Current plan — only for a seller whose plan a payment actually activated. */}
        {paid && (
          <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: '24px 28px', marginBottom: 28 }}>
            <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-2)', textTransform: 'uppercase', marginBottom: 12 }}>Текущий тариф</p>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <span style={{ fontSize: 26, fontWeight: 700, color: 'var(--text)' }}>{planLabel(user?.plan)}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Clock size={13} color="var(--text-2)" />
              <span style={{ fontSize: 13, color: 'var(--text-2)' }}>До {fmt(user!.subscription_end_date!)}</span>
            </div>
          </div>
        )}

        {/* Honest unavailable-monetization state — no tariff cards, no buy button, no charge. */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: '24px 28px', marginBottom: 28 }}>
          <p style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', margin: '0 0 8px' }}>
            Платные тарифы пока недоступны
          </p>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.6, margin: 0 }}>
            PULT сейчас работает в режиме Advisory MVP. Платные тарифы будут доступны позже.
          </p>
        </div>

        {/* Payment history — real records only */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, overflow: 'hidden' }}>
          <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--line)' }}>
            <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.1em', color: 'var(--text-2)', textTransform: 'uppercase', margin: 0 }}>История платежей</p>
          </div>
          {history.length === 0 ? (
            <div style={{ padding: '40px 24px', textAlign: 'center' }}>
              <p style={{ fontSize: 14, color: 'var(--text-2)', margin: 0 }}>Платежей пока нет</p>
            </div>
          ) : (
            <div>
              {history.map((p, i) => (
                <div key={p.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 24px', borderBottom: i < history.length - 1 ? '1px solid var(--line)' : 'none', flexWrap: 'wrap', gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 2 }}>
                      Тариф {PLAN_LABELS[p.plan] ?? p.plan}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-2)' }}>{fmt(p.created_at)}</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
                      {Number(p.amount).toLocaleString('ru-RU')} ₽
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: STATUS_COLORS[p.status] ?? 'var(--text-2)', background: `${STATUS_COLORS[p.status] ?? 'var(--text-2)'}14`, borderRadius: 6, padding: '3px 10px', border: `1px solid ${STATUS_COLORS[p.status] ?? 'var(--text-2)'}30` }}>
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

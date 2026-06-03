'use client'
import { useEffect, useState, useRef } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { CheckCircle2, XCircle, Clock, Loader2, ArrowRight } from 'lucide-react'
import { api } from '@/lib/api'
import Link from 'next/link'

type Status = 'loading' | 'succeeded' | 'pending' | 'canceled' | 'error'

const MESSAGES: Record<Status, { title: string; sub: string }> = {
  loading:   { title: 'Проверяем платёж…',       sub: 'Подождите несколько секунд' },
  succeeded: { title: 'Оплата прошла успешно!',   sub: 'Тариф активирован. Добро пожаловать!' },
  pending:   { title: 'Платёж обрабатывается',    sub: 'Это может занять до минуты. Обновите страницу позже.' },
  canceled:  { title: 'Платёж отменён',           sub: 'Средства не были списаны. Попробуйте снова.' },
  error:     { title: 'Что-то пошло не так',      sub: 'Не удалось проверить статус платежа.' },
}

const ICONS: Record<Status, React.ReactNode> = {
  loading:   <Loader2 size={48} color="#7C3AED" style={{ animation: 'spin 1s linear infinite' }} />,
  succeeded: <CheckCircle2 size={48} color="#10B981" />,
  pending:   <Clock size={48} color="#F59E0B" />,
  canceled:  <XCircle size={48} color="#EF4444" />,
  error:     <XCircle size={48} color="#EF4444" />,
}

export default function PaymentResultPage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const [status, setStatus] = useState<Status>('loading')
  const polled = useRef(false)

  useEffect(() => {
    if (polled.current) return
    polled.current = true

    const paymentId = searchParams.get('payment_id') ?? searchParams.get('paymentId')

    if (!paymentId) {
      setStatus('error')
      return
    }

    let attempts = 0
    const maxAttempts = 8
    const interval = 2000

    async function poll() {
      try {
        const res = await api.payments.status(paymentId!)
        if (res.status === 'succeeded') {
          // Refresh user in localStorage
          try {
            const stored = localStorage.getItem('user')
            if (stored) {
              const u = JSON.parse(stored)
              u.plan = res.tariff === 'pro' ? 'profi' : 'master'
              localStorage.setItem('user', JSON.stringify(u))
            }
          } catch {}
          // subscription_started is recorded server-side in _activate_plan
          // (webhook + status poll) — the reliable source of truth.
          setStatus('succeeded')
        } else if (res.status === 'canceled') {
          setStatus('canceled')
        } else if (attempts < maxAttempts) {
          attempts++
          setTimeout(poll, interval)
        } else {
          setStatus('pending')
        }
      } catch {
        setStatus('error')
      }
    }

    poll()
  }, [searchParams])

  const msg = MESSAGES[status]

  return (
    <div style={{ minHeight: '100vh', background: '#0A0A0A', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Inter, Arial, sans-serif', padding: '20px' }}>
      <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 20, padding: '48px 40px', maxWidth: 440, width: '100%', textAlign: 'center' }}>

        <div style={{ marginBottom: 24 }}>
          {ICONS[status]}
        </div>

        <h1 style={{ fontSize: 22, fontWeight: 700, color: '#FFFFFF', margin: '0 0 10px' }}>{msg.title}</h1>
        <p style={{ fontSize: 14, color: '#A0A0A0', margin: '0 0 36px', lineHeight: 1.6 }}>{msg.sub}</p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {status === 'succeeded' && (
            <Link href="/dashboard" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: '#7C3AED', color: '#FFFFFF', fontWeight: 700, fontSize: 15, padding: '14px 24px', borderRadius: 12, textDecoration: 'none' }}>
              В личный кабинет
              <ArrowRight size={16} />
            </Link>
          )}
          {(status === 'canceled' || status === 'error') && (
            <Link href="/dashboard/billing" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: '#7C3AED', color: '#FFFFFF', fontWeight: 700, fontSize: 15, padding: '14px 24px', borderRadius: 12, textDecoration: 'none' }}>
              Попробовать снова
              <ArrowRight size={16} />
            </Link>
          )}
          {status === 'pending' && (
            <button
              onClick={() => { setStatus('loading'); polled.current = false }}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: '#7C3AED', color: '#FFFFFF', fontWeight: 700, fontSize: 15, padding: '14px 24px', borderRadius: 12, border: 'none', cursor: 'pointer', width: '100%' }}
            >
              Обновить статус
            </button>
          )}
          <Link href="/dashboard" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#A0A0A0', fontSize: 14, textDecoration: 'none' }}>
            На главную
          </Link>
        </div>

      </div>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

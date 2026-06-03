'use client'

import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { setToken } from '@/lib/session'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

function VerifyEmailContent() {
  const router = useRouter()
  const params = useSearchParams()
  const token  = params.get('token') ?? ''

  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setMessage('Ссылка для подтверждения недействительна.')
      return
    }
    api.auth.verifyEmail(token)
      .then(data => {
        setToken(data.access_token, data.user)
        setStatus('success')
        setTimeout(() => router.push('/dashboard'), 2000)
      })
      .catch(err => {
        setStatus('error')
        setMessage(err instanceof Error ? err.message : 'Ссылка недействительна или уже использована.')
      })
  }, [token, router])

  return (
    <div
      className="min-h-screen flex items-center justify-center px-5"
      style={{ background: '#F8F9FA', position: 'relative', overflow: 'hidden' }}
    >
      <div aria-hidden style={{ position: 'fixed', top: '5%', right: '-8%', width: 380, height: 380, background: 'radial-gradient(circle, rgba(26,115,232,0.06) 0%, transparent 65%)', animation: 'orbDrift 18s ease-in-out infinite', filter: 'blur(44px)', pointerEvents: 'none', zIndex: 0 }} />
      <div aria-hidden style={{ position: 'fixed', bottom: '10%', left: '-6%', width: 300, height: 300, background: 'radial-gradient(circle, rgba(26,115,232,0.04) 0%, transparent 65%)', animation: 'orbDrift2 22s ease-in-out infinite', filter: 'blur(38px)', pointerEvents: 'none', zIndex: 0 }} />
      <div className="w-full max-w-[420px]">
        <div className="text-center mb-10">
          <Link href="/" className="inline-flex items-baseline">
            <span className="font-bold tracking-tight" style={{ fontSize: '2rem', color: '#202124' }}>Бизнес‑</span>
            <span className="font-bold tracking-tight text-gradient-gold" style={{ fontSize: '2rem' }}>Пульт</span>
          </Link>
        </div>

        <Card className="p-9 text-center">
          {status === 'loading' && (
            <>
              <div className="flex justify-center mb-5">
                <Loader2 size={36} style={{ color: '#1A73E8' }} className="animate-spin" />
              </div>
              <h2 className="font-semibold mb-2" style={{ fontSize: '1.25rem', color: '#202124' }}>
                Подтверждаем email...
              </h2>
              <p style={{ color: '#5F6368', fontSize: '0.9375rem' }}>Пожалуйста, подождите</p>
            </>
          )}

          {status === 'success' && (
            <>
              <div className="flex justify-center mb-5">
                <div
                  className="w-14 h-14 rounded-full flex items-center justify-center"
                  style={{ background: 'rgba(26,115,232,0.1)', border: '2px solid rgba(26,115,232,0.3)' }}
                >
                  <CheckCircle2 size={28} style={{ color: '#1A73E8' }} />
                </div>
              </div>
              <h2 className="font-semibold mb-2" style={{ fontSize: '1.25rem', color: '#202124' }}>
                Email подтверждён!
              </h2>
              <p style={{ color: '#5F6368', fontSize: '0.9375rem', marginBottom: 16 }}>
                Переходим в панель управления...
              </p>
              <Loader2 size={18} className="animate-spin mx-auto" style={{ color: '#1A73E8' }} />
            </>
          )}

          {status === 'error' && (
            <>
              <div className="flex justify-center mb-5">
                <div
                  className="w-14 h-14 rounded-full flex items-center justify-center"
                  style={{ background: 'rgba(220,38,38,0.08)', border: '2px solid rgba(220,38,38,0.2)' }}
                >
                  <XCircle size={28} style={{ color: '#DC2626' }} />
                </div>
              </div>
              <h2 className="font-semibold mb-2" style={{ fontSize: '1.25rem', color: '#202124' }}>
                Не удалось подтвердить
              </h2>
              <p style={{ color: '#5F6368', fontSize: '0.9375rem', marginBottom: 24 }}>
                {message}
              </p>
              <Button size="lg" className="w-full" asChild>
                <Link href="/register">Зарегистрироваться заново</Link>
              </Button>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center" style={{ background: '#F8F9FA' }}>
        <Loader2 size={32} style={{ color: '#1A73E8' }} className="animate-spin" />
      </div>
    }>
      <VerifyEmailContent />
    </Suspense>
  )
}

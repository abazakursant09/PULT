'use client'

import { useState } from 'react'
import Link from 'next/link'
import authStyles from '../auth.module.css'
import { Send, ArrowLeft, KeyRound } from 'lucide-react'
import { PultMark } from '@/components/brand/PultMark'
import { api } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

export default function ForgotPasswordPage() {
  const [email,   setEmail]   = useState('')
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')
  const [result,  setResult]  = useState<{ message: string } | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await api.auth.forgotPassword(email)
      setResult(data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Ошибка')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className={`${authStyles.surface} min-h-screen flex items-center justify-center px-5 py-16 relative overflow-hidden`}

    >
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse 80% 55% at 50% -5%, rgba(26,115,232,0.12) 0%, transparent 65%)' }}
      />
      <div className={`${authStyles.width} w-full max-w-[440px] animate-fade-in relative`} style={{ zIndex: 2 }}>
        <div className="text-center mb-10">
          <Link href="/" className={authStyles.brand}>
            <PultMark approvedReference width={30} height={35} style={{ color: 'var(--violet-text)' }} />
            <span className="font-bold tracking-tight text-gradient-gold" style={{ fontSize: '2.25rem' }}>Пульт</span>
          </Link>
        </div>

        <Card className={`${authStyles.panel} p-9 sm:p-11`}>
          <div className="flex items-center gap-3 mb-8">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.16)' }}
            >
              <KeyRound size={18} style={{ color: 'var(--violet-text)' }} />
            </div>
            <div>
              <h1 className="font-semibold" style={{ fontSize: '1.5rem', color: 'var(--text)', lineHeight: 1.2 }}>
                Восстановление пароля
              </h1>
              <p style={{ fontSize: '0.875rem', color: 'var(--text-2)', marginTop: 2 }}>Введите email от аккаунта</p>
            </div>
          </div>

          {error && (
            <div
              className="mb-6 px-4 py-3 rounded-xl"
              style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: 'var(--danger)', fontSize: '0.875rem' }}
            >
              {error}
            </div>
          )}

          {!result ? (
            <form onSubmit={submit} className="space-y-5">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  autoFocus
                />
              </div>
              <Button
                type="submit"
                loading={loading}
                className="w-full mt-2"
                size="lg"
              >
                {!loading && <><Send size={15} /> Получить ссылку</>}
              </Button>
            </form>
          ) : (
            <div>
              <div
                className="rounded-xl p-4"
                style={{ background: 'rgba(26,115,232,0.06)', border: '1px solid rgba(26,115,232,0.2)' }}
              >
                <p style={{ color: 'var(--violet-text)', fontSize: '0.9375rem' }}>{result.message}</p>
              </div>
            </div>
          )}

          <Separator className="my-8" />

          <p className="text-center" style={{ fontSize: '0.9375rem', color: 'var(--text-2)' }}>
            <Link href="/login" className="flex items-center justify-center gap-1.5 font-medium hover:opacity-80 transition-opacity" style={{ color: 'var(--violet-text)' }}>
              <ArrowLeft size={14} /> Вернуться ко входу
            </Link>
          </p>
        </Card>
      </div>
    </div>
  )
}

'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Send, ArrowLeft, KeyRound, ExternalLink } from 'lucide-react'
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
  const [result,  setResult]  = useState<{ message: string; reset_token: string | null } | null>(null)

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
      className="min-h-screen flex items-center justify-center px-5 py-16 relative overflow-hidden"
      style={{ background: '#F8F9FA' }}
    >
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ background: 'radial-gradient(ellipse 80% 55% at 50% -5%, rgba(26,115,232,0.12) 0%, transparent 65%)' }}
      />
      <div aria-hidden style={{ position: 'fixed', top: '10%', right: '-8%', width: 360, height: 360, background: 'radial-gradient(circle, rgba(26,115,232,0.055) 0%, transparent 65%)', animation: 'orbDrift 18s ease-in-out infinite', filter: 'blur(44px)', pointerEvents: 'none', zIndex: 0 }} />
      <div aria-hidden style={{ position: 'fixed', bottom: '15%', left: '-6%', width: 280, height: 280, background: 'radial-gradient(circle, rgba(26,115,232,0.04) 0%, transparent 65%)', animation: 'orbDrift2 22s ease-in-out infinite', filter: 'blur(38px)', pointerEvents: 'none', zIndex: 0 }} />

      <div className="w-full max-w-[440px] animate-fade-in relative" style={{ zIndex: 2 }}>
        <div className="text-center mb-10">
          <Link href="/" className="inline-flex items-baseline">
            <span className="font-bold tracking-tight" style={{ fontSize: '2.25rem', color: '#202124' }}>Бизнес‑</span>
            <span className="font-bold tracking-tight text-gradient-gold" style={{ fontSize: '2.25rem' }}>Пульт</span>
          </Link>
        </div>

        <Card className="p-9 sm:p-11">
          <div className="flex items-center gap-3 mb-8">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.16)' }}
            >
              <KeyRound size={18} style={{ color: '#1A73E8' }} />
            </div>
            <div>
              <h2 className="font-semibold" style={{ fontSize: '1.5rem', color: '#202124', lineHeight: 1.2 }}>
                Восстановление пароля
              </h2>
              <p style={{ fontSize: '0.875rem', color: 'rgba(0,0,0,0.38)', marginTop: 2 }}>Введите email от аккаунта</p>
            </div>
          </div>

          {error && (
            <div
              className="mb-6 px-4 py-3 rounded-xl"
              style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626', fontSize: '0.875rem' }}
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
                className="rounded-xl p-4 mb-5"
                style={{ background: 'rgba(26,115,232,0.06)', border: '1px solid rgba(26,115,232,0.2)' }}
              >
                <p style={{ color: '#1A73E8', fontSize: '0.9375rem' }}>{result.message}</p>
              </div>

              {result.reset_token && (
                <div>
                  <p style={{ fontSize: '0.875rem', color: '#8A8986', marginBottom: 12 }}>
                    В реальной системе ссылка отправляется на почту. В демо-режиме используйте ссылку ниже:
                  </p>
                  <Link
                    href={`/reset-password?token=${encodeURIComponent(result.reset_token)}`}
                    className="btn btn-primary w-full"
                    style={{ paddingTop: 14, paddingBottom: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                  >
                    Перейти к сбросу пароля <ExternalLink size={14} />
                  </Link>
                  <p className="mt-3" style={{ fontSize: '0.75rem', color: 'rgba(0,0,0,0.38)', wordBreak: 'break-all' }}>
                    <code style={{ color: '#1A73E8' }}>/reset-password?token={result.reset_token}</code>
                  </p>
                </div>
              )}
            </div>
          )}

          <Separator className="my-8" />

          <p className="text-center" style={{ fontSize: '0.9375rem', color: '#8A8986' }}>
            <Link href="/login" className="flex items-center justify-center gap-1.5 font-medium hover:opacity-80 transition-opacity" style={{ color: '#1A73E8' }}>
              <ArrowLeft size={14} /> Вернуться ко входу
            </Link>
          </p>
        </Card>
      </div>
    </div>
  )
}

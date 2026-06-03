'use client'

import { Suspense, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { KeyRound, ArrowRight, CheckCircle2, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

function ResetPasswordContent() {
  const router = useRouter()
  const params = useSearchParams()
  const token  = params.get('token') ?? ''

  const [form,    setForm]    = useState({ password: '', confirm: '' })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')
  const [done,    setDone]    = useState(false)

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (form.password !== form.confirm) { setError('Пароли не совпадают'); return }
    if (form.password.length < 8)       { setError('Пароль — минимум 8 символов'); return }
    if (!token)                         { setError('Токен сброса отсутствует'); return }

    setLoading(true)
    try {
      await api.auth.resetPassword(token, form.password)
      setDone(true)
      setTimeout(() => router.push('/login'), 2500)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Ошибка сброса пароля')
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <div className="min-h-screen flex items-center justify-center px-5" style={{ background: '#F8F9FA' }}>
        <Card className="p-10 text-center w-full max-w-[400px]">
          <div className="flex justify-center mb-5">
            <div
              className="w-14 h-14 rounded-full flex items-center justify-center"
              style={{ background: 'rgba(26,115,232,0.1)', border: '2px solid rgba(26,115,232,0.3)' }}
            >
              <CheckCircle2 size={28} style={{ color: '#1A73E8' }} />
            </div>
          </div>
          <h2 className="font-semibold mb-2" style={{ fontSize: '1.25rem', color: '#202124' }}>Пароль изменён!</h2>
          <p style={{ color: '#8A8986', fontSize: '0.9375rem' }}>Переходим на страницу входа...</p>
          <Loader2 size={18} className="animate-spin mx-auto mt-4" style={{ color: '#1A73E8' }} />
        </Card>
      </div>
    )
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
                Новый пароль
              </h2>
              <p style={{ fontSize: '0.875rem', color: 'rgba(0,0,0,0.38)', marginTop: 2 }}>Придумайте надёжный пароль</p>
            </div>
          </div>

          {!token && (
            <div
              className="mb-6 px-4 py-3 rounded-xl"
              style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626', fontSize: '0.875rem' }}
            >
              Недействительная ссылка сброса. Запросите новую на странице восстановления.
            </div>
          )}

          {error && (
            <div
              className="mb-6 px-4 py-3 rounded-xl"
              style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626', fontSize: '0.875rem' }}
            >
              {error}
            </div>
          )}

          <form onSubmit={submit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="password">Новый пароль</Label>
              <Input
                id="password"
                type="password"
                value={form.password}
                onChange={set('password')}
                placeholder="Минимум 8 символов"
                required
                autoFocus
                disabled={!token}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">Подтвердите пароль</Label>
              <Input
                id="confirm"
                type="password"
                value={form.confirm}
                onChange={set('confirm')}
                placeholder="••••••••"
                required
                disabled={!token}
              />
            </div>
            <Button
              type="submit"
              loading={loading}
              disabled={!token}
              className="w-full mt-2"
              size="lg"
            >
              {!loading && <>Сохранить пароль <ArrowRight size={16} /></>}
            </Button>
          </form>

          <Separator className="my-8" />
          <p className="text-center" style={{ fontSize: '0.9375rem', color: '#8A8986' }}>
            <Link href="/forgot-password" className="font-medium hover:opacity-80 transition-opacity" style={{ color: '#1A73E8' }}>
              Запросить новую ссылку
            </Link>
          </p>
        </Card>
      </div>
    </div>
  )
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center" style={{ background: '#F8F9FA' }}>
        <Loader2 size={32} style={{ color: '#1A73E8' }} className="animate-spin" />
      </div>
    }>
      <ResetPasswordContent />
    </Suspense>
  )
}

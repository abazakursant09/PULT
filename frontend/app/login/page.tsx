'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowRight, Shield } from 'lucide-react'
import { api } from '@/lib/api'
import { setToken } from '@/lib/session'
import { useLang } from '@/lib/lang-context'
import { OAuthButtons } from '@/components/OAuthButtons'

type Step = 'credentials' | 'mfa'

export default function LoginPage() {
  const router = useRouter()
  const { t } = useLang()

  const [step,     setStep]     = useState<Step>('credentials')
  const [form,     setForm]     = useState({ email: '', password: '' })
  const [remember, setRemember] = useState(false)
  const [mfaCode,  setMfaCode]  = useState('')
  const [mfaToken, setMfaToken] = useState('')
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const [info,     setInfo]     = useState('')
  const [redirectTo, setRedirectTo] = useState('/dashboard')

  useEffect(() => {
    const saved = localStorage.getItem('bp_remember_email')
    if (saved) { setForm(f => ({ ...f, email: saved })); setRemember(true) }
    const params = new URLSearchParams(window.location.search)
    if (params.get('reason') === 'session_expired') {
      setInfo('Сессия истекла. Войдите снова.')
    }
    const from = params.get('from')
    if (from && from.startsWith('/')) setRedirectTo(from)
  }, [])

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  async function submitCredentials(e: React.FormEvent) {
    e.preventDefault(); setError(''); setLoading(true)
    try {
      const data = await api.auth.login(form.email, form.password)
      if ('mfa_required' in data && data.mfa_required) {
        setMfaToken(data.mfa_token); setStep('mfa'); return
      }
      if (remember) localStorage.setItem('bp_remember_email', form.email)
      else localStorage.removeItem('bp_remember_email')
      const auth = data as import('@/lib/api').AuthResponse
      setToken(auth.access_token, auth.user)
      router.push(redirectTo)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally { setLoading(false) }
  }

  async function submitMfa(e: React.FormEvent) {
    e.preventDefault(); setError(''); setLoading(true)
    try {
      const data = await api.mfa.loginMfa(mfaToken, mfaCode)
      setToken(data.access_token, data.user)
      router.push(redirectTo)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally { setLoading(false) }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6" style={{ background: '#09090B' }}>
      {/* Subtle grid */}
      <div className="fixed inset-0 hero-grid pointer-events-none" style={{ opacity: 0.6 }} />

      <div className="w-full max-w-[380px] animate-fade-in" style={{ position: 'relative' }}>

        {/* Logo mark */}
        <div className="flex flex-col items-center mb-9">
          <div style={{
            width: 52, height: 52, borderRadius: 14,
            background: '#111113',
            border: '1px solid #232329',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            marginBottom: 18,
            boxShadow: '0 0 0 1px rgba(124,58,237,0.15), 0 8px 24px rgba(0,0,0,0.5)',
          }}>
            {/* ПУЛЬТ icon */}
            <svg width="26" height="26" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="10" cy="10" r="8.25" stroke="#A78BFA" strokeWidth="1.5"/>
              <circle cx="10" cy="10" r="4" stroke="#A78BFA" strokeWidth="1" strokeOpacity="0.45"/>
              <circle cx="10" cy="10" r="1.75" fill="#A78BFA"/>
            </svg>
          </div>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: '#FFFFFF', letterSpacing: '-0.03em', lineHeight: 1, marginBottom: 6 }}>
            ПУЛЬТ
          </h1>
          <p style={{ fontSize: 12.5, color: '#52525B', letterSpacing: '0.03em', textAlign: 'center' }}>
            Центр управления бизнесом на маркетплейсах
          </p>
        </div>

        {/* Session expired info */}
        {info && (
          <div className="mb-4 px-4 py-3 rounded-[8px] text-[13px]"
               style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.2)', color: '#A78BFA' }}>
            {info}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mb-5 px-4 py-3 rounded-[8px] text-[13px]"
               style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: '#FCA5A5' }}>
            {error}
          </div>
        )}

        {step === 'credentials' && (
          <>
            <OAuthButtons mode="login" />

            <div className="flex items-center gap-3 my-5">
              <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.08)' }} />
              <span className="text-[12px]" style={{ color: '#71717A' }}>или продолжите через почту</span>
              <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.08)' }} />
            </div>

            <form onSubmit={submitCredentials} className="space-y-4">
              <div>
                <label className="label mb-2">EMAIL</label>
                <input
                  className="input"
                  type="email"
                  placeholder="you@example.com"
                  value={form.email}
                  onChange={set('email')}
                  required
                  autoFocus
                  autoComplete="email"
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="label" style={{ marginBottom: 0 }}>ПАРОЛЬ</label>
                  <Link
                    href="/forgot-password"
                    className="text-[11px] transition-colors duration-200"
                    style={{ color: '#71717A', textDecoration: 'none' }}
                    onMouseEnter={e => { e.currentTarget.style.color = '#7C3AED' }}
                    onMouseLeave={e => { e.currentTarget.style.color = '#71717A' }}
                  >
                    Забыли пароль?
                  </Link>
                </div>
                <input
                  className="input"
                  type="password"
                  placeholder="••••••••"
                  value={form.password}
                  onChange={set('password')}
                  required
                  autoComplete="current-password"
                  style={{ width: '100%' }}
                />
              </div>

              <label className="flex items-center gap-2.5 cursor-pointer select-none" style={{ marginTop: 4 }}>
                <div
                  onClick={() => setRemember(v => !v)}
                  className="flex items-center justify-center w-4 h-4 rounded-[4px] transition-all cursor-pointer shrink-0"
                  style={{
                    background: remember ? '#A78BFA' : 'transparent',
                    border: `1.5px solid ${remember ? '#A78BFA' : 'rgba(255,255,255,0.15)'}`,
                  }}
                >
                  {remember && (
                    <svg width="9" height="7" viewBox="0 0 11 8" fill="none">
                      <path d="M1 4L4 7L10 1" stroke="#FFFFFF" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
                <span
                  className="text-[13px]"
                  style={{ color: '#71717A' }}
                  onClick={() => setRemember(v => !v)}
                >
                  Запомнить меня
                </span>
              </label>

              <button
                type="submit"
                className="btn btn-primary"
                disabled={loading}
                style={{ width: '100%', marginTop: 8 }}
              >
                {loading
                  ? <span className="spinner" />
                  : <>{t('login.submit')} <ArrowRight size={15} /></>
                }
              </button>
            </form>

            <p className="text-center mt-6 text-[13px]" style={{ color: '#71717A' }}>
              Нет аккаунта?{' '}
              <Link
                href="/register"
                style={{ color: '#A78BFA', textDecoration: 'none' }}
                onMouseEnter={e => { e.currentTarget.style.opacity = '0.8' }}
                onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
              >
                {t('login.register')}
              </Link>
            </p>
          </>
        )}

        {step === 'mfa' && (
          <form onSubmit={submitMfa} className="space-y-4">
            <div
              className="flex items-center gap-3 mb-2 p-4 rounded-[8px]"
              style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}
            >
              <div
                className="w-9 h-9 rounded-[8px] flex items-center justify-center shrink-0"
                style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.20)' }}
              >
                <Shield size={16} style={{ color: '#A78BFA' }} />
              </div>
              <div>
                <p className="text-[14px] font-semibold" style={{ color: '#FFFFFF' }}>{t('mfa.title')}</p>
                <p className="text-[12px]" style={{ color: '#71717A' }}>{t('mfa.subtitle')}</p>
              </div>
            </div>

            <div>
              <label className="label mb-2">{t('mfa.code')}</label>
              <input
                className="input mono text-center"
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                value={mfaCode}
                onChange={e => setMfaCode(e.target.value.replace(/\D/g, ''))}
                placeholder="000000"
                required
                autoFocus
                style={{ width: '100%', fontSize: 28, letterSpacing: '0.4em', height: 56 }}
              />
              <p className="text-[11px] mt-1.5 text-center" style={{ color: '#71717A' }}>{t('mfa.hint')}</p>
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || mfaCode.length !== 6}
              style={{ width: '100%' }}
            >
              {loading
                ? <span className="spinner" />
                : <><Shield size={15} /> {t('mfa.submit')}</>
              }
            </button>

            <button
              type="button"
              className="btn btn-ghost"
              style={{ width: '100%' }}
              onClick={() => { setStep('credentials'); setError(''); setMfaCode('') }}
            >
              {t('mfa.back')}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}

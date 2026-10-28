'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import authStyles from '../auth.module.css'
import { useRouter } from 'next/navigation'
import { ArrowRight, Shield } from 'lucide-react'
import { PultMark } from '@/components/brand/PultMark'
import { api } from '@/lib/api'
import { setUser } from '@/lib/session'
import { useLang } from '@/lib/lang-context'

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
    // Accept root-relative application paths only. Backslashes/control characters
    // and dot-segment normalization can turn a seemingly local path into //host.
    if (from && from.startsWith('/') && !/[\\\u0000-\u0020\u007f]/.test(from)) {
      try {
        const target = new URL(from, window.location.origin)
        if (target.origin === window.location.origin && !target.pathname.startsWith('//')) {
          setRedirectTo(target.pathname + target.search + target.hash)
        }
      } catch { /* Invalid return paths retain the dashboard default. */ }
    }
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
      setUser(auth.user)
      router.push(redirectTo)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally { setLoading(false) }
  }

  async function submitMfa(e: React.FormEvent) {
    e.preventDefault(); setError(''); setLoading(true)
    try {
      const data = await api.mfa.loginMfa(mfaToken, mfaCode)
      setUser(data.user)
      router.push(redirectTo)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally { setLoading(false) }
  }

  return (
    <div className={`${authStyles.surface} min-h-screen flex items-center justify-center p-6`} >

      <div className={`${authStyles.width} w-full max-w-[380px] animate-fade-in`} style={{ position: 'relative' }}>

        <div className={authStyles.brand}>
          <PultMark approvedReference width={29} height={33} />
          <span className={authStyles.brandName}>ПУЛЬТ</span>
        </div>
        <div className={authStyles.panel}>
          <h1 className={authStyles.heading}>{step === 'mfa' ? 'Подтвердите вход' : 'Войти в Пульт'}</h1>
          <p className={authStyles.lead}>Операционная система селлера</p>

        {/* Session expired info */}
        {info && (
          <div role="status" className="mb-4 px-4 py-3 rounded-[8px] text-[13px]"
               style={{ background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.2)', color: 'var(--violet-text)' }}>
            {info}
          </div>
        )}

        {/* Error */}
        {error && (
          <div role="alert" className="mb-5 px-4 py-3 rounded-[8px] text-[13px]"
               style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--danger)' }}>
            {error}
            {/* Login is blocked until email is verified. Rather than dead-end the seller, offer
                a resend right here, using the email they just typed. */}
            {/Подтвердите email/i.test(error) && (
              <button
                type="button"
                onClick={async () => {
                  if (!form.email.trim()) return
                  try { await api.auth.resendVerification(form.email.trim()) } catch {}
                  // Conditional, not a promise: the endpoint answers the same for every address
                  // so it cannot be used to discover which ones are registered.
                  setError('')
                  setInfo('Если адрес зарегистрирован и почта доступна, письмо будет отправлено. '
                        + 'Проверьте папку «Спам» или попробуйте позже.')
                }}
                className="block mt-2 underline"
                style={{ color: 'var(--danger)', fontSize: '12.5px' }}
              >
                Отправить письмо для подтверждения ещё раз
              </button>
            )}
          </div>
        )}

        {step === 'credentials' && (
          <>

            <form method="post" onSubmit={submitCredentials} className="space-y-4">
              <div>
                <label htmlFor="login-email" className="label mb-2">EMAIL</label>
                <input
                  id="login-email"
                  name="email"
                  className="input"
                  type="email"
                  placeholder="you@example.com"
                  value={form.email}
                  onChange={set('email')}
                  required
                  autoFocus
                  autoComplete="email"
                  spellCheck={false}
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label htmlFor="login-password" className="label" style={{ marginBottom: 0 }}>ПАРОЛЬ</label>
                  <Link
                    href="/forgot-password"
                    className="text-[11px] transition-colors duration-200"
                    style={{ color: 'var(--text-2)', textDecoration: 'none' }}
                    onMouseEnter={e => { e.currentTarget.style.color = '#7C3AED' }}
                    onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-2)' }}
                  >
                    Забыли пароль?
                  </Link>
                </div>
                <input
                  id="login-password"
                  name="password"
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
                <input
                  id="login-remember-email"
                  name="remember-email"
                  type="checkbox"
                  className="peer sr-only"
                  checked={remember}
                  onChange={e => setRemember(e.target.checked)}
                />
                <div
                  aria-hidden="true"
                  className="flex items-center justify-center w-4 h-4 rounded-[4px] cursor-pointer shrink-0 peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--violet-text)]"
                  style={{
                    background: remember ? 'var(--violet-text)' : 'transparent',
                    border: `1.5px solid ${remember ? 'var(--violet-text)' : 'var(--line)'}`,
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
                  style={{ color: 'var(--text-2)' }}
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

            <p className="text-center mt-6 text-[13px]" style={{ color: 'var(--text-2)' }}>
              Нет аккаунта?{' '}
              <Link
                href="/register"
                style={{ color: 'var(--violet-text)', textDecoration: 'none' }}
                onMouseEnter={e => { e.currentTarget.style.opacity = '0.8' }}
                onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
              >
                {t('login.register')}
              </Link>
            </p>
          </>
        )}

        {step === 'mfa' && (
          <form method="post" onSubmit={submitMfa} className="space-y-4">
            <div
              className="flex items-center gap-3 mb-2 p-4 rounded-[8px]"
              style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}
            >
              <div
                className="w-9 h-9 rounded-[8px] flex items-center justify-center shrink-0"
                style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.20)' }}
              >
                <Shield size={16} style={{ color: 'var(--violet-text)' }} />
              </div>
              <div>
                <p className="text-[14px] font-semibold" style={{ color: 'var(--text)' }}>{t('mfa.title')}</p>
                <p className="text-[12px]" style={{ color: 'var(--text-2)' }}>{t('mfa.subtitle')}</p>
              </div>
            </div>

            <div>
              <label htmlFor="login-mfa-code" className="label mb-2">{t('mfa.code')}</label>
              <input
                id="login-mfa-code"
                name="mfa-code"
                autoComplete="one-time-code"
                spellCheck={false}
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
              <p className="text-[11px] mt-1.5 text-center" style={{ color: 'var(--text-2)' }}>{t('mfa.hint')}</p>
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
    </div>
  )
}

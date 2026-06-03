'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowRight, MailCheck, ExternalLink, CheckCircle2 } from 'lucide-react'
import { api } from '@/lib/api'
import { trackEvent, stampFunnel, FUNNEL_TS } from '@/lib/events'
import { useLang } from '@/lib/lang-context'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'
import { OAuthButtons } from '@/components/OAuthButtons'
import { MathCaptcha } from '@/components/MathCaptcha'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { BlurFade } from '@/components/ui/blur-fade'

export default function RegisterPage() {
  const { t } = useLang()
  const router = useRouter()

  const [form,        setForm]        = useState({ name: '', email: '', password: '', confirm: '' })
  const [agreed,      setAgreed]      = useState(false)
  const [agreedTerms, setAgreedTerms] = useState(false)
  const [error,       setError]       = useState('')
  const [loading,     setLoading]     = useState(false)
  const [verifyToken, setVerifyToken] = useState<string | null>(null)
  const [refCode,     setRefCode]     = useState<string | null>(null)
  const [captchaOk,   setCaptchaOk]   = useState(false)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const ref = params.get('ref')
    if (ref) setRefCode(ref.toUpperCase().slice(0, 8))
    const savedEmail = params.get('email')
    if (savedEmail) setForm(f => ({ ...f, email: savedEmail }))
  }, [])

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const passwordStrength = (() => {
    const p = form.password
    if (!p) return 0
    let score = 0
    if (p.length >= 8) score += 25
    if (/[A-ZА-ЯЁ]/.test(p)) score += 25
    if (/\d/.test(p)) score += 25
    if (/[^a-zA-ZА-ЯЁа-яё0-9]/.test(p)) score += 25
    return score
  })()

  const strengthLabel = passwordStrength === 0 ? '' : passwordStrength <= 25 ? 'Слабый' : passwordStrength <= 50 ? 'Средний' : passwordStrength <= 75 ? 'Хороший' : 'Надёжный'
  const strengthColor = passwordStrength <= 25 ? '#ef4444' : passwordStrength <= 50 ? '#f97316' : passwordStrength <= 75 ? '#eab308' : '#22c55e'

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!agreed)                               { setError('Необходимо дать согласие на обработку данных'); return }
    if (!agreedTerms)                          { setError('Необходимо принять условия Пользовательского соглашения'); return }
    if (form.password !== form.confirm)        { setError('Пароли не совпадают'); return }
    if (!captchaOk)                            { setError('Решите антибот-задачу'); return }
    if (form.password.length < 8)             { setError('Пароль — минимум 8 символов'); return }
    if (!/[A-ZА-ЯЁ]/.test(form.password))     { setError('Пароль должен содержать хотя бы одну заглавную букву'); return }
    if (!/\d/.test(form.password))             { setError('Пароль должен содержать хотя бы одну цифру'); return }
    setLoading(true)
    trackEvent('registration_started', 'auth')
    try {
      const data = await api.auth.register(form.email, form.name, form.password, refCode ?? undefined)
      setVerifyToken(data.verification_token)
      stampFunnel(FUNNEL_TS.signup)              // anchor for time_to_first_* activation metrics
      trackEvent('registration_completed', 'auth')
      trackEvent('trial_started', 'auth', undefined, { plan: 'trial' })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setLoading(false)
    }
  }

  function CheckBox({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
    return (
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className="flex items-center justify-center w-4 h-4 rounded shrink-0 transition-all mt-0.5"
        style={{ background: checked ? '#1A73E8' : 'transparent', border: `1.5px solid ${checked ? '#1A73E8' : 'hsl(var(--border))'}` }}
      >
        {checked && <svg width="9" height="7" viewBox="0 0 11 8" fill="none"><path d="M1 4L4 7L10 1" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>}
      </button>
    )
  }

  if (verifyToken) {
    const verifyUrl = `/verify-email?token=${encodeURIComponent(verifyToken)}`
    return (
      <div className="min-h-screen flex items-center justify-center px-5 py-16" style={{ background: '#F6F9FC' }}>
        <BlurFade className="w-full max-w-[460px]" inView>
          <div className="text-center mb-8">
            <Link href="/" className="inline-flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-lg" style={{ background: '#1A73E8', color: '#fff' }}>П</div>
              <span className="font-bold text-xl tracking-tight" style={{ color: '#0A2540' }}>ПУЛЬТ</span>
            </Link>
          </div>
          <Card className="shadow-stripe-lg">
            <CardContent className="p-8">
              <div className="flex items-center justify-center mb-6">
                <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(26,115,232,0.1)', border: '1px solid rgba(26,115,232,0.2)' }}>
                  <MailCheck size={28} style={{ color: '#1A73E8' }} />
                </div>
              </div>
              <h2 className="text-center font-bold text-2xl mb-2" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>{t('verify.title')}</h2>
              <p className="text-center text-muted-foreground mb-6" style={{ lineHeight: 1.7 }}>
                {t('verify.body')} <strong style={{ color: '#0A2540' }}>{form.email}</strong>. {t('verify.demo')}
              </p>
              <a href={verifyUrl} className="btn btn-primary w-full flex items-center justify-center gap-2">
                {t('verify.btn')} <ExternalLink size={14} />
              </a>
              <p className="text-center mt-4 text-xs text-muted-foreground">
                {t('verify.copy')} <code className="text-primary break-all">{verifyUrl}</code>
              </p>
            </CardContent>
          </Card>
        </BlurFade>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-5 py-12" style={{ background: '#F6F9FC' }}>

      <div className="absolute top-5 right-5 z-20">
        <LanguageSwitcher />
      </div>

      <BlurFade className="w-full max-w-[460px]" inView delay={0.05}>

        <div className="text-center mb-8">
          <Link href="/" className="inline-flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center font-bold text-lg" style={{ background: '#1A73E8', color: '#fff' }}>П</div>
            <span className="font-bold text-xl tracking-tight" style={{ color: '#0A2540' }}>ПУЛЬТ</span>
          </Link>
          <p className="mt-2 text-sm text-muted-foreground">{t('common.marketplace')}</p>
        </div>

        {/* Progress indicator */}
        <div className="flex items-center gap-2 mb-6 px-1">
          {['Личные данные', 'Пароль', 'Согласия'].map((label, i) => (
            <div key={i} className="flex items-center gap-2 flex-1">
              <div className="flex items-center gap-1.5">
                <div className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-semibold" style={{ background: '#1A73E8', color: 'white' }}>
                  {i + 1}
                </div>
                <span className="text-xs font-medium hidden sm:block" style={{ color: '#425466' }}>{label}</span>
              </div>
              {i < 2 && <div className="flex-1 h-px" style={{ background: 'hsl(var(--border))' }} />}
            </div>
          ))}
        </div>

        <Card className="shadow-stripe-lg border-border/60">
          <CardHeader className="pb-4">
            <CardTitle className="text-2xl font-bold" style={{ letterSpacing: '-0.02em', color: '#0A2540' }}>
              {t('register.title')}
            </CardTitle>
            <CardDescription style={{ color: '#425466' }}>{t('register.subtitle')}</CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">

            {refCode && (
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm" style={{ background: 'rgba(26,115,232,0.06)', border: '1px solid rgba(26,115,232,0.18)' }}>
                <CheckCircle2 size={15} style={{ color: '#1A73E8', flexShrink: 0 }} />
                <span style={{ color: '#425466' }}>
                  Реферальный код: <strong className="font-mono" style={{ color: '#1A73E8' }}>{refCode}</strong>
                </span>
              </div>
            )}

            {error && (
              <div className="px-4 py-3 rounded-lg text-sm" style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#b91c1c' }}>
                {error}
              </div>
            )}

            <OAuthButtons mode="register" />

            <div className="relative">
              <Separator />
              <span className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2 bg-background px-2 text-xs text-muted-foreground">или</span>
            </div>

            <form onSubmit={submit} className="space-y-4">
              <div className="space-y-1.5">
                <Label>{t('register.name')}</Label>
                <Input type="text" value={form.name} onChange={set('name')} placeholder="Иван Петров" required autoFocus />
              </div>
              <div className="space-y-1.5">
                <Label>{t('register.email')}</Label>
                <Input type="email" value={form.email} onChange={set('email')} placeholder="you@example.com" required />
              </div>
              <div className="space-y-1.5">
                <Label>{t('register.password')}</Label>
                <Input type="password" value={form.password} onChange={set('password')} placeholder={t('register.passHint')} required />
                {form.password && (
                  <div className="space-y-1">
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'hsl(var(--border))' }}>
                      <div className="h-full rounded-full transition-all duration-300" style={{ width: `${passwordStrength}%`, background: strengthColor }} />
                    </div>
                    <p className="text-xs" style={{ color: strengthColor }}>{strengthLabel}</p>
                  </div>
                )}
              </div>
              <div className="space-y-1.5">
                <Label>{t('register.confirm')}</Label>
                <Input type="password" value={form.confirm} onChange={set('confirm')} placeholder="••••••••" required />
              </div>

              <MathCaptcha onValid={setCaptchaOk} />

              <label className="flex items-start gap-2.5 cursor-pointer" onClick={() => setAgreed(v => !v)}>
                <CheckBox checked={agreed} onChange={setAgreed} />
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {t('register.privacyText')}{' '}
                  <a href="/privacy" target="_blank" rel="noopener noreferrer" className="hover:underline" style={{ color: '#1A73E8' }} onClick={e => e.stopPropagation()}>
                    {t('register.privacy')}
                  </a>
                </span>
              </label>

              <label className="flex items-start gap-2.5 cursor-pointer" onClick={() => setAgreedTerms(v => !v)}>
                <CheckBox checked={agreedTerms} onChange={setAgreedTerms} />
                <span className="text-xs leading-relaxed text-muted-foreground">
                  {t('register.termsText')}{' '}
                  <a href="/terms" target="_blank" rel="noopener noreferrer" className="hover:underline" style={{ color: '#1A73E8' }} onClick={e => e.stopPropagation()}>
                    {t('register.terms')}
                  </a>
                </span>
              </label>

              <p className="text-xs text-muted-foreground">{t('register.age')}</p>

              <Button type="submit" className="w-full" size="lg" loading={loading} disabled={!agreed || !agreedTerms || !captchaOk}>
                {!loading && <>{t('register.submit')}<ArrowRight size={15} className="ml-1.5" /></>}
              </Button>
            </form>

            <p className="text-center text-sm text-muted-foreground">
              {t('register.hasAccount')}{' '}
              <Link href="/login" className="font-semibold hover:underline" style={{ color: '#1A73E8' }}>
                {t('register.login')}
              </Link>
            </p>
          </CardContent>
        </Card>

        <p className="text-center mt-5 text-xs text-muted-foreground">{t('register.tagline')}</p>
      </BlurFade>
    </div>
  )
}

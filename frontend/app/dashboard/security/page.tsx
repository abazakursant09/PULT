'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Shield, ShieldCheck, ShieldOff, Copy, Check, RefreshCw, AlertTriangle, ExternalLink, ArrowLeft,
} from 'lucide-react'
import { api } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

type Phase = 'idle' | 'setup' | 'verify' | 'disable'

export default function SecurityPage() {
  const router = useRouter()

  const [mfaEnabled,  setMfaEnabled]  = useState(false)
  const [loading,     setLoading]     = useState(true)
  const [phase,       setPhase]       = useState<Phase>('idle')
  const [secret,      setSecret]      = useState('')
  const [otpauth,     setOtpauth]     = useState('')
  const [code,        setCode]        = useState('')
  const [working,     setWorking]     = useState(false)
  const [error,       setError]       = useState('')
  const [success,     setSuccess]     = useState('')
  const [copied,      setCopied]      = useState(false)

  useEffect(() => {
    if (!localStorage.getItem('token')) { router.push('/login'); return }
    api.mfa.status()
      .then(s => setMfaEnabled(s.enabled))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [router])

  async function startSetup() {
    setError(''); setWorking(true)
    try {
      const data = await api.mfa.setup()
      setSecret(data.secret)
      setOtpauth(data.otpauth)
      setPhase('setup')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка')
    } finally {
      setWorking(false)
    }
  }

  async function verifyCode() {
    if (code.length !== 6) return
    setError(''); setWorking(true)
    try {
      await api.mfa.verify(code)
      setMfaEnabled(true)
      setPhase('idle')
      setCode('')
      setSuccess('MFA успешно включена — аккаунт защищён двухфакторной аутентификацией')
      setTimeout(() => setSuccess(''), 5000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Неверный код')
    } finally {
      setWorking(false)
    }
  }

  async function disableMfa() {
    if (code.length !== 6) return
    setError(''); setWorking(true)
    try {
      await api.mfa.disable(code)
      setMfaEnabled(false)
      setPhase('idle')
      setCode('')
      setSuccess('MFA отключена')
      setTimeout(() => setSuccess(''), 4000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Неверный код')
    } finally {
      setWorking(false)
    }
  }

  function copySecret() {
    navigator.clipboard.writeText(secret).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg)' }}>
        <RefreshCw size={28} className="animate-spin" style={{ color: 'var(--text-3)' }} />
      </div>
    )
  }

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)', position: 'relative' }}>
      <div aria-hidden style={{ position: 'fixed', top: '8%', right: '-5%', width: 360, height: 360, background: 'radial-gradient(circle, rgba(110,106,252,0.06) 0%, transparent 65%)', filter: 'blur(44px)', pointerEvents: 'none', zIndex: 0 }} />
      <main className="max-w-[720px] mx-auto px-5 sm:px-8 py-10 sm:py-14 animate-fade-in" style={{ position: 'relative', zIndex: 1 }}>

        <div className="mb-10">
          <button onClick={() => router.push('/dashboard/account?tab=security')}
                  className="sm:hidden flex items-center gap-1.5 text-sm mb-4 rounded-lg px-2.5 py-1.5"
                  style={{ color: 'var(--text-3)', border: '1px solid rgba(255,255,255,0.08)', background: 'none', cursor: 'pointer' }}>
            <ArrowLeft size={13} /> Назад
          </button>
          <p className="text-xs font-medium mb-2.5 uppercase tracking-wide" style={{ color: 'var(--violet)' }}>Настройки</p>
          <h1 className="font-bold tracking-tight mb-2" style={{ fontSize: 'clamp(1.75rem,3vw,2.25rem)', color: '#FFFFFF' }}>
            Безопасность
          </h1>
          <p style={{ fontSize: '1rem', color: 'var(--text-3)' }}>
            Управляйте двухфакторной аутентификацией и защитой аккаунта
          </p>
        </div>

        {success && (
          <div className="mb-6 px-4 py-3 rounded-xl flex items-center gap-3 animate-slide-up"
               style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.25)', color: 'var(--violet)' }}>
            <Check size={16} /> {success}
          </div>
        )}
        {error && (
          <div className="mb-6 px-4 py-3 rounded-xl flex items-center gap-3"
               style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.22)', color: 'var(--danger)' }}>
            <AlertTriangle size={16} /> {error}
          </div>
        )}

        <Card className="p-7 sm:p-9 mb-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-6">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl flex items-center justify-center shrink-0"
                   style={{ background: mfaEnabled ? 'rgba(110,106,252,0.12)' : 'rgba(110,106,252,0.08)', border: `1px solid ${mfaEnabled ? 'rgba(110,106,252,0.28)' : 'rgba(110,106,252,0.16)'}` }}>
                {mfaEnabled
                  ? <ShieldCheck size={22} style={{ color: 'var(--violet)' }} />
                  : <Shield      size={22} style={{ color: 'var(--violet)' }} />
                }
              </div>
              <div>
                <h2 className="font-semibold text-base" style={{ color: '#FFFFFF' }}>
                  Двухфакторная аутентификация (2FA)
                </h2>
                <p className="text-sm mt-0.5" style={{ color: 'var(--text-3)' }}>
                  {mfaEnabled ? 'Аккаунт защищён TOTP-кодом при каждом входе' : 'Дополнительная защита аккаунта не активирована'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3 shrink-0">
              <Badge variant={mfaEnabled ? 'outline' : 'secondary'}
                     style={mfaEnabled ? { background: 'rgba(110,106,252,0.12)', color: 'var(--violet)', borderColor: 'rgba(110,106,252,0.28)' } : {}}>
                {mfaEnabled ? '✓ Включено' : 'Выключено'}
              </Badge>

              {phase === 'idle' && (
                mfaEnabled ? (
                  <Button variant="ghost" size="sm"
                          onClick={() => { setPhase('disable'); setCode(''); setError('') }}>
                    <ShieldOff size={14} /> Отключить
                  </Button>
                ) : (
                  <Button onClick={startSetup} loading={working}>
                    {!working && <><Shield size={14} /> Включить 2FA</>}
                  </Button>
                )
              )}
            </div>
          </div>
        </Card>

        {phase === 'setup' && (
          <Card className="p-7 sm:p-9 animate-slide-up mb-6">
            <h3 className="font-semibold text-base mb-1" style={{ color: '#FFFFFF' }}>
              Настройка приложения аутентификатора
            </h3>
            <p className="text-sm mb-6" style={{ color: 'var(--text-3)' }}>
              Откройте <strong style={{ color: '#FFFFFF' }}>Google Authenticator</strong>,{' '}
              <strong style={{ color: '#FFFFFF' }}>Authy</strong> или{' '}
              <strong style={{ color: '#FFFFFF' }}>любое совместимое приложение</strong> и добавьте аккаунт вручную или по ссылке.
            </p>

            <div className="mb-5">
              <Label className="mb-2 block" style={{ color: 'var(--text-3)' }}>Секретный ключ (ввести вручную)</Label>
              <div className="flex items-center gap-3 px-4 py-3 rounded-xl"
                   style={{ background: 'rgba(110,106,252,0.06)', border: '1px solid rgba(110,106,252,0.18)' }}>
                <code className="flex-1 text-sm font-semibold tracking-widest break-all font-mono" style={{ color: 'var(--violet)' }}>
                  {secret}
                </code>
                <Button variant="ghost" size="icon" onClick={copySecret} title="Скопировать">
                  {copied ? <Check size={14} style={{ color: 'var(--violet)' }} /> : <Copy size={14} />}
                </Button>
              </div>
            </div>

            <div className="mb-8">
              <a href={otpauth} className="inline-flex items-center gap-2 text-sm font-medium" style={{ color: 'var(--violet)' }}>
                <ExternalLink size={13} />
                Открыть в приложении аутентификатора
              </a>
            </div>

            <div className="mb-8 rounded-xl px-5 py-4 space-y-2" style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
              {[
                'Установите Google Authenticator, Authy или 1Password',
                'Нажмите «+» → «Ввести ключ вручную»',
                'Введите секретный ключ выше и название «Бизнес-Пульт»',
                'Введите 6-значный код ниже для подтверждения',
              ].map((s, i) => (
                <div key={i} className="flex items-start gap-3">
                  <span className="w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 mt-0.5"
                        style={{ background: 'rgba(110,106,252,0.15)', color: 'var(--violet)' }}>
                    {i + 1}
                  </span>
                  <p className="text-sm" style={{ color: 'var(--text-3)' }}>{s}</p>
                </div>
              ))}
            </div>

            <div className="mb-5 space-y-2">
              <Label htmlFor="totp-setup" style={{ color: 'var(--text-3)' }}>Код подтверждения (6 цифр)</Label>
              <Input
                id="totp-setup"
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                placeholder="000000"
                className="font-mono text-center"
                style={{ fontSize: '1.75rem', letterSpacing: '0.35em' }}
                autoFocus
              />
            </div>

            <div className="flex gap-3">
              <Button variant="ghost" onClick={() => { setPhase('idle'); setCode(''); setError('') }}>Отмена</Button>
              <Button className="flex-1" onClick={verifyCode} disabled={working || code.length !== 6} loading={working}>
                {!working && <><ShieldCheck size={14} /> Подтвердить и включить</>}
              </Button>
            </div>
          </Card>
        )}

        {phase === 'disable' && (
          <Card className="p-7 sm:p-9 animate-slide-up mb-6">
            <div className="flex items-center gap-3 mb-5">
              <AlertTriangle size={18} style={{ color: 'var(--danger)' }} />
              <h3 className="font-semibold text-base" style={{ color: '#FFFFFF' }}>
                Отключение двухфакторной аутентификации
              </h3>
            </div>
            <p className="text-sm mb-6" style={{ color: 'var(--text-3)' }}>
              После отключения вход будет защищён только паролем. Введите текущий код из приложения для подтверждения.
            </p>

            <div className="mb-5 space-y-2">
              <Label htmlFor="totp-disable" style={{ color: 'var(--text-3)' }}>Код аутентификатора</Label>
              <Input
                id="totp-disable"
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                placeholder="000000"
                className="font-mono text-center"
                style={{ fontSize: '1.75rem', letterSpacing: '0.35em' }}
                autoFocus
              />
            </div>

            <div className="flex gap-3">
              <Button variant="ghost" onClick={() => { setPhase('idle'); setCode(''); setError('') }}>Отмена</Button>
              <Button className="flex-1" variant="ghost"
                      onClick={disableMfa} disabled={working || code.length !== 6} loading={working}
                      style={{ color: 'var(--danger)', borderColor: 'rgba(239,68,68,0.25)' }}>
                {!working && <><ShieldOff size={14} /> Отключить 2FA</>}
              </Button>
            </div>
          </Card>
        )}

        {phase === 'idle' && (
          <div className="rounded-2xl px-6 py-5 mt-4" style={{ background: 'rgba(110,106,252,0.06)', border: '1px solid rgba(110,106,252,0.14)' }}>
            <h3 className="text-sm font-semibold mb-3" style={{ color: '#FFFFFF' }}>
              Зачем нужна двухфакторная аутентификация?
            </h3>
            <ul className="space-y-1.5">
              {[
                'Защищает аккаунт даже при утечке пароля',
                'Использует одноразовые коды — действуют 30 секунд',
                'Совместима с Google Authenticator, Authy, 1Password',
                'Не требует интернета для генерации кодов',
              ].map((item, i) => (
                <li key={i} className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-3)' }}>
                  <span style={{ color: 'var(--violet)', flexShrink: 0 }}>·</span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}

      </main>
    </div>
  )
}

'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useLang } from '@/lib/lang-context'
import { api } from '@/lib/api'
import { setToken } from '@/lib/session'
import { trackEvent, stampFunnel, FUNNEL_TS } from '@/lib/events'
import { X } from 'lucide-react'

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" fill="none">
      <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
      <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
    </svg>
  )
}

function AppleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" fill="currentColor">
      <path d="M14.045 9.556c-.02-2.044 1.67-3.03 1.745-3.076C14.709 4.543 12.94 4.3 12.312 4.285c-1.49-.153-2.924.886-3.681.886-.771 0-1.95-.867-3.209-.843C3.876 4.352 2.319 5.385 1.464 6.949c-1.743 3.016-.447 7.466 1.24 9.908.827 1.194 1.808 2.53 3.09 2.48 1.247-.05 1.713-.8 3.217-.8 1.49 0 1.921.8 3.226.774 1.336-.025 2.18-1.206 2.998-2.406a11.01 11.01 0 0 0 1.363-2.784c-.032-.012-2.602-1-2.553-3.565zm-2.392-6.543c.672-.826 1.126-1.97.997-3.113-.966.04-2.15.651-2.845 1.461-.617.712-1.165 1.882-.96 2.994 1.086.083 2.2-.545 2.808-1.342z"/>
    </svg>
  )
}

function YandexIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" fill="none">
      <circle cx="9" cy="9" r="9" fill="#FC3F1D"/>
      <path d="M10.27 14h-1.6V7.38H7.8C6.9 7.38 6.43 7.87 6.43 8.7c0 .95.43 1.39 1.28 1.96L8.76 11.5 6.35 14H4.62l2.6-3.3C5.9 9.88 5.16 9.01 5.16 7.67c0-1.7 1.18-2.8 3.16-2.8H10.27V14z" fill="white"/>
    </svg>
  )
}

type Provider = 'google' | 'apple' | 'yandex'

interface Props {
  mode: 'login' | 'register'
}

export function OAuthButtons({ mode }: Props) {
  const { t } = useLang()
  const router = useRouter()

  const [modal,   setModal]   = useState<{ provider: Provider; label: string } | null>(null)
  const [email,   setEmail]   = useState('')
  const [name,    setName]    = useState('')
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  const providers: { id: Provider; label: () => string; icon: React.ReactNode }[] = [
    { id: 'google', label: () => t('oauth.google'), icon: <GoogleIcon /> },
    { id: 'apple',  label: () => t('oauth.apple'),  icon: <AppleIcon /> },
    { id: 'yandex', label: () => t('oauth.yandex'), icon: <YandexIcon /> },
  ]

  function openModal(p: Provider, label: string) {
    setModal({ provider: p, label })
    setEmail('')
    setName('')
    setError('')
  }

  async function handleOAuth(e: React.FormEvent) {
    e.preventDefault()
    if (!modal) return
    setError('')
    setLoading(true)
    if (mode === 'register') trackEvent('registration_started', 'auth', undefined, { method: 'oauth', provider: modal.provider })
    try {
      const providerUserId = `${modal.provider}_stub_${Date.now()}`
      const data = await api.auth.oauthLogin({
        provider:         modal.provider,
        provider_user_id: providerUserId,
        email:            email.trim() || undefined,
        name:             name.trim()  || undefined,
      })
      setToken(data.access_token, data.user)
      if (mode === 'register') {
        stampFunnel(FUNNEL_TS.signup)
        trackEvent('registration_completed', 'auth', undefined, { method: 'oauth', provider: modal.provider })
        trackEvent('trial_started', 'auth', undefined, { plan: 'trial', method: 'oauth' })
      }
      setModal(null)
      router.push('/dashboard')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <div className="flex flex-col gap-2.5">
        {providers.map(p => (
          <button
            key={p.id}
            type="button"
            onClick={() => openModal(p.id, p.label())}
            className="btn btn-ghost"
            style={{ width: '100%', gap: 10 }}
            onMouseEnter={e => {
              e.currentTarget.style.borderColor = '#A78BFA'
              e.currentTarget.style.color = '#A78BFA'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.borderColor = '#1A1A1A'
              e.currentTarget.style.color = '#FFFFFF'
            }}
          >
            {p.icon}
            {p.label()}
          </button>
        ))}
      </div>

      {modal && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center px-4"
          style={{ background: 'rgba(0,0,0,0.7)' }}
          onClick={e => { if (e.target === e.currentTarget) setModal(null) }}
        >
          <div
            className="w-full max-w-sm p-6 rounded-[8px] animate-fade-in"
            style={{ background: '#0F0F0F', border: '1px solid #1A1A1A' }}
          >
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-3">
                <div
                  className="w-8 h-8 rounded-[8px] flex items-center justify-center shrink-0"
                  style={{ background: 'rgba(124,58,237,0.10)', border: '1px solid rgba(124,58,237,0.20)' }}
                >
                  <span style={{ fontSize: '1rem' }}>
                    {modal.provider === 'google' ? '🔑' : modal.provider === 'apple' ? '' : '🟠'}
                  </span>
                </div>
                <div>
                  <p className="text-[14px] font-semibold" style={{ color: '#FFFFFF' }}>{t('oauth.modalTitle')}</p>
                  <p className="text-[12px]" style={{ color: '#8A8A8A' }}>{modal.label}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setModal(null)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#5A5A5A', padding: 4 }}
                onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
                onMouseLeave={e => { e.currentTarget.style.color = '#5A5A5A' }}
              >
                <X size={16} />
              </button>
            </div>

            <p className="text-[13px] mb-5" style={{ color: '#8A8A8A', lineHeight: 1.65 }}>
              {t('oauth.modalBody')}
            </p>

            {error && (
              <div className="mb-4 px-4 py-3 rounded-[8px] text-[13px]"
                   style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: '#FCA5A5' }}>
                {error}
              </div>
            )}

            <form onSubmit={handleOAuth} className="space-y-4">
              <div>
                <label className="label mb-2">{t('oauth.emailLabel')}</label>
                <input
                  type="email"
                  required
                  className="input"
                  placeholder="you@example.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  autoFocus
                  style={{ width: '100%' }}
                />
              </div>
              <div>
                <label className="label mb-2">{t('oauth.nameLabel')}</label>
                <input
                  type="text"
                  className="input"
                  placeholder="Ivan Petrov"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <div className="flex gap-3 pt-1">
                <button type="submit" disabled={loading} className="btn btn-primary" style={{ flex: 1 }}>
                  {loading
                    ? <><span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> {t('oauth.loading')}</>
                    : t('oauth.continue')
                  }
                </button>
                <button type="button" onClick={() => setModal(null)} className="btn btn-ghost">
                  {t('oauth.cancel')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}

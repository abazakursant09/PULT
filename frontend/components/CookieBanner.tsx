'use client'
import { useState, useEffect } from 'react'
import { Cookie, X } from 'lucide-react'

type Consent = { analytics: boolean; timestamp: number }

export function CookieBanner() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const saved = localStorage.getItem('cookie_consent')
    if (!saved) setVisible(true)
  }, [])

  function accept(analytics: boolean) {
    const consent: Consent = { analytics, timestamp: Date.now() }
    localStorage.setItem('cookie_consent', JSON.stringify(consent))
    // Set technical cookie (always)
    document.cookie = 'bp_session=1; path=/; SameSite=Strict; Secure; max-age=31536000'
    if (analytics) {
      document.cookie = 'bp_analytics=1; path=/; SameSite=Strict; Secure; max-age=31536000'
    }
    setVisible(false)
  }

  if (!visible) return null

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-[9999] animate-slide-up"
      style={{ padding: '0 16px 16px' }}
    >
      <div
        className="max-w-3xl mx-auto rounded-2xl p-5"
        style={{
          background: '#111113',
          border: '1px solid rgba(255,255,255,0.10)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
        }}
      >
        <div className="flex items-start gap-4">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'rgba(128,128,255,0.12)', color: '#A78BFA' }}
          >
            <Cookie size={20} />
          </div>

          <div className="flex-1 min-w-0">
            <p className="font-semibold text-sm mb-1" style={{ color: '#FFFFFF' }}>
              Мы используем файлы cookie
            </p>
            <p className="text-sm" style={{ color: '#71717A' }}>
              Технические cookie необходимы для работы сервиса. Аналитические cookie помогают нам улучшать продукт — мы не передаём данные третьим лицам.
            </p>
          </div>

          <button
            onClick={() => accept(false)}
            className="shrink-0 p-1.5 rounded-lg transition-colors"
            style={{ color: '#71717A' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '' }}
            aria-label="Закрыть"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex items-center gap-3 mt-4 flex-wrap">
          <button
            className="btn btn-primary"
            style={{ height: 40, fontSize: '0.875rem', padding: '0 20px' }}
            onClick={() => accept(true)}
          >
            Принять все
          </button>
          <button
            className="btn btn-ghost"
            style={{ height: 40, fontSize: '0.875rem', padding: '0 20px' }}
            onClick={() => accept(false)}
          >
            Только необходимые
          </button>
          <a
            href="/privacy"
            className="text-sm"
            style={{ color: '#A78BFA', textDecoration: 'none', marginLeft: 'auto' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.textDecoration = 'underline' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.textDecoration = 'none' }}
          >
            Политика конфиденциальности
          </a>
        </div>
      </div>
    </div>
  )
}

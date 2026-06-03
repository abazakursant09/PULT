'use client'

import { useEffect } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error(error)
  }, [error])

  return (
    <html lang="ru">
      <body style={{ margin: 0, background: '#F8F9FA', fontFamily: 'system-ui, sans-serif' }}>
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '20px',
          }}
        >
          <div
            style={{
              background: 'rgba(22,22,28,0.9)',
              border: '1px solid rgba(0,0,0,0.07)',
              borderRadius: 20,
              boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
              padding: '40px 36px',
              maxWidth: 420,
              width: '100%',
              textAlign: 'center',
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: 16,
                background: 'rgba(26,115,232,0.08)',
                border: '1px solid rgba(26,115,232,0.18)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 24px',
              }}
            >
              <AlertTriangle size={26} color="#2563EB" />
            </div>

            <h1 style={{ fontSize: '1.375rem', fontWeight: 700, color: '#202124', margin: '0 0 8px' }}>
              Критическая ошибка
            </h1>
            <p style={{ fontSize: '0.9375rem', color: '#5F6368', lineHeight: 1.65, margin: '0 0 32px' }}>
              Приложение столкнулось с критической ошибкой. Попробуйте перезагрузить страницу.
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <button
                onClick={reset}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8,
                  padding: '12px 24px',
                  borderRadius: 12,
                  background: 'linear-gradient(135deg, #2563EB 0%, #2563EB 100%)',
                  color: '#202124',
                  fontWeight: 600,
                  fontSize: '1rem',
                  border: 'none',
                  cursor: 'pointer',
                  boxShadow: '0 4px 16px rgba(26,115,232,0.3)',
                }}
              >
                <RefreshCw size={15} /> Перезагрузить
              </button>
              <a
                href="/"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8,
                  padding: '12px 24px',
                  borderRadius: 12,
                  background: 'rgba(0,0,0,0.05)',
                  color: '#5F6368',
                  fontWeight: 500,
                  fontSize: '1rem',
                  border: '1px solid rgba(0,0,0,0.12)',
                  textDecoration: 'none',
                }}
              >
                На главную
              </a>
            </div>
          </div>
        </div>
      </body>
    </html>
  )
}

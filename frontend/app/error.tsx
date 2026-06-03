'use client'

import { useEffect } from 'react'
import { AlertTriangle, RefreshCw, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Error({
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
    <div
      className="min-h-screen flex items-center justify-center px-5"
      style={{ background: '#F8F9FA' }}
    >
      <div
        className="w-full max-w-[440px] rounded-2xl p-10 text-center"
        style={{
          background: 'rgba(22,22,28,0.9)',
          border: '1px solid rgba(0,0,0,0.07)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
        }}
      >
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-6"
          style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.18)' }}
        >
          <AlertTriangle size={26} style={{ color: '#1A73E8' }} />
        </div>

        <h1
          className="font-bold mb-2"
          style={{ fontSize: '1.375rem', color: '#202124' }}
        >
          Что-то пошло не так
        </h1>
        <p className="mb-8" style={{ fontSize: '0.9375rem', color: '#5F6368', lineHeight: 1.65 }}>
          Произошла внутренняя ошибка. Мы уже работаем над её исправлением.
        </p>

        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Button onClick={() => { reset(); window.location.reload() }}>
            <RefreshCw size={15} /> Обновить страницу
          </Button>
          <a
            href="/"
            className="btn btn-ghost"
            style={{ padding: '11px 24px' }}
          >
            <ArrowLeft size={15} /> На главную
          </a>
        </div>
      </div>
    </div>
  )
}

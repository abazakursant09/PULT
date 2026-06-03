'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { FileQuestion, ArrowLeft, Home } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function NotFound() {
  const router = useRouter()

  return (
    <div
      className="min-h-screen flex items-center justify-center px-5"
      style={{ background: '#09090B' }}
    >
      <div
        className="w-full max-w-[440px] rounded-2xl p-10 text-center"
        style={{
          background: '#111113',
          border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        }}
      >
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-6"
          style={{ background: 'rgba(128,128,255,0.10)', border: '1px solid rgba(128,128,255,0.20)' }}
        >
          <FileQuestion size={26} style={{ color: '#A78BFA' }} />
        </div>

        <p
          className="font-bold mb-1"
          style={{ fontSize: '3rem', color: '#A78BFA', lineHeight: 1 }}
        >
          404
        </p>
        <h1
          className="font-bold mb-2"
          style={{ fontSize: '1.375rem', color: '#FFFFFF' }}
        >
          Страница не найдена
        </h1>
        <p className="mb-8" style={{ fontSize: '0.9375rem', color: '#71717A', lineHeight: 1.65 }}>
          Запрошенная страница не существует или была перемещена.
        </p>

        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <Button onClick={() => router.back()}>
            <ArrowLeft size={15} /> Вернуться
          </Button>
          <Link
            href="/"
            className="btn btn-ghost"
            style={{ padding: '11px 24px' }}
          >
            <Home size={15} /> На главную
          </Link>
        </div>
      </div>
    </div>
  )
}

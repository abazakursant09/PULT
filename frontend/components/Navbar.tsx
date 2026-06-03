'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowLeft, LogOut, Zap } from 'lucide-react'
import { type User } from '@/lib/api'
import { clearSession } from '@/lib/session'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'

interface NavbarProps {
  user?: User | null
  showBack?: boolean
  backHref?: string
}

export function Navbar({ user, showBack, backHref = '/dashboard' }: NavbarProps) {
  const router = useRouter()

  function logout() {
    clearSession()
    router.push('/login')
  }

  return (
    <header className="navbar sticky top-0 z-40">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 h-14 sm:h-16 flex items-center justify-between gap-4">

        <div className="flex items-center gap-3">
          {showBack && (
            <Link
              href={backHref}
              className="w-8 h-8 flex items-center justify-center rounded-lg transition-all duration-200"
              style={{ border: '1px solid rgba(255,255,255,0.08)', color: '#71717A' }}
              onMouseEnter={e => {
                const el = e.currentTarget as HTMLElement
                el.style.color = '#FFFFFF'
                el.style.borderColor = 'rgba(110,106,252,0.4)'
                el.style.background = 'rgba(110,106,252,0.08)'
              }}
              onMouseLeave={e => {
                const el = e.currentTarget as HTMLElement
                el.style.color = '#71717A'
                el.style.borderColor = 'rgba(255,255,255,0.08)'
                el.style.background = 'transparent'
              }}
            >
              <ArrowLeft size={14} />
            </Link>
          )}

          <Link href="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
                 style={{ background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.2)' }}>
              <Zap size={14} style={{ color: '#7C3AED' }} />
            </div>
            <div className="flex items-baseline">
              <span className="font-bold text-[1.05rem] tracking-tight" style={{ color: '#FFFFFF' }}>Бизнес‑</span>
              <span className="font-bold text-[1.05rem] tracking-tight" style={{ color: '#7C3AED' }}>Пульт</span>
            </div>
          </Link>
        </div>

        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          {user && (
            <>
              <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg"
                   style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0"
                     style={{ background: 'rgba(110,106,252,0.15)', color: '#7C3AED' }}>
                  {user.name.charAt(0).toUpperCase()}
                </div>
                <span className="text-sm" style={{ color: '#71717A' }}>{user.name}</span>
              </div>
              <button
                onClick={logout}
                className="btn btn-ghost gap-1.5"
                style={{ padding: '7px 12px', fontSize: '0.8rem' }}
                title="Выйти"
              >
                <LogOut size={13} />
                <span className="hidden sm:inline">Выйти</span>
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  )
}

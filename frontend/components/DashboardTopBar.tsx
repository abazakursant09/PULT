'use client'

import { useEffect, useState, useRef } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { Settings, LogOut, ChevronDown, Home, ArrowLeft } from 'lucide-react'
import { NotificationBell } from '@/components/NotificationBell'
import { type User } from '@/lib/api'
import { clearSession } from '@/lib/session'

const BREADCRUMBS: Record<string, string> = {
  '/dashboard':                 'Пульт',
  '/profit-calculator':         'Калькулятор',
  '/auto-promotions':           'Автоакции',
  '/dashboard/seo-cards':       'SEO-карточки',
  '/ai-agents':                 'ИИ-агенты',
  '/community':                 'Сообщество',
  '/suppliers':                 'Производители',
  '/logistics':                 'Логистика',
  '/market-overview':           'Обзор рынка',
  '/dashboard/settings':        'Настройки',
  '/dashboard/security':        'Безопасность',
  '/dashboard/finance':         'Финансы',
  '/dashboard/chat':            'Чат',
  '/dashboard/account':         'Аккаунт',
  '/dashboard/monitor':         'Мониторинг',
  '/dashboard/referrals':       'Рефералы',
  '/dashboard/marking':         'Маркировка',
  '/dashboard/notifications':   'Уведомления',
  '/dashboard/billing':         'Подписка',
}

export function DashboardTopBar() {
  const router   = useRouter()
  const pathname = usePathname()
  const [user, setUser] = useState<User | null>(null)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const s = localStorage.getItem('user')
    if (s) try { setUser(JSON.parse(s)) } catch {}
  }, [])

  useEffect(() => {
    if (!open) return
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  function logout() { clearSession(); router.push('/login') }

  const crumb = pathname ? (BREADCRUMBS[pathname] ?? pathname.split('/').filter(Boolean).pop() ?? '') : ''
  const isRoot = pathname === '/dashboard'

  const menuItemStyle: React.CSSProperties = {
    color: '#A1A1AA', textDecoration: 'none', display: 'flex',
    alignItems: 'center', gap: 8,
  }

  return (
    <header
      className="hidden sm:flex sticky top-0 z-30 items-center justify-between px-5 shrink-0"
      style={{
        height: 48,
        background: 'rgba(9,9,11,0.90)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid #232329',
      }}
    >
      {/* Left: breadcrumb */}
      <div className="flex items-center gap-2">
        {!isRoot && (
          <button
            onClick={() => router.back()}
            style={{
              display: 'flex', alignItems: 'center',
              background: 'none', border: 'none', cursor: 'pointer',
              color: '#52525B', padding: '3px 5px', borderRadius: 5,
              transition: 'color 0.15s ease, background 0.15s ease',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = '#A1A1AA'; e.currentTarget.style.background = 'rgba(255,255,255,0.05)' }}
            onMouseLeave={e => { e.currentTarget.style.color = '#52525B'; e.currentTarget.style.background = 'none' }}
          >
            <ArrowLeft size={13} />
          </button>
        )}

        <div className="flex items-center gap-1.5" style={{ fontSize: 12, letterSpacing: '0.02em' }}>
          <Link
            href="/"
            style={{ color: '#3F3F46', textDecoration: 'none', transition: 'color 0.15s ease', display: 'flex', alignItems: 'center' }}
            onMouseEnter={e => { e.currentTarget.style.color = '#71717A' }}
            onMouseLeave={e => { e.currentTarget.style.color = '#3F3F46' }}
          >
            <Home size={11} />
          </Link>
          <span style={{ color: '#27272A' }}>/</span>
          <span style={{ color: '#52525B', fontWeight: 500, letterSpacing: '0.05em', fontSize: 11, textTransform: 'uppercase' }}>
            ПУЛЬТ
          </span>
          {crumb && crumb !== 'Пульт' && <>
            <span style={{ color: '#27272A' }}>/</span>
            <span style={{ color: '#71717A' }}>{crumb}</span>
          </>}
        </div>
      </div>

      {/* Right: notifications + user */}
      <div className="flex items-center gap-2">
        <NotificationBell dropdownSide="down" />

        {user && (
          <div className="relative" ref={ref}>
            <button
              onClick={() => setOpen(v => !v)}
              className="flex items-center gap-1.5"
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                padding: '4px 6px', borderRadius: 7,
                transition: 'background 0.15s ease',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'none' }}
            >
              <div style={{
                width: 26, height: 26, borderRadius: 7,
                background: '#18181B',
                border: '1px solid #232329',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700, color: '#A78BFA',
              }}>
                {user.name.charAt(0).toUpperCase()}
              </div>
              <span style={{
                fontSize: 12, color: '#71717A',
                maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
                className="hidden lg:block"
              >
                {user.name}
              </span>
              <ChevronDown size={11} style={{ color: '#52525B' }} />
            </button>

            {open && (
              <div
                className="absolute right-0 top-full mt-1.5 w-44 rounded-[8px] overflow-hidden z-50 py-1"
                style={{ background: '#111113', border: '1px solid #232329', boxShadow: '0 8px 24px rgba(0,0,0,0.5)' }}
              >
                <Link
                  href="/"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] transition-colors duration-150"
                  style={menuItemStyle}
                  onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF'; e.currentTarget.style.background = '#18181B' }}
                  onMouseLeave={e => { e.currentTarget.style.color = '#A1A1AA'; e.currentTarget.style.background = 'transparent' }}
                >
                  <Home size={13} /> На главную
                </Link>
                <Link
                  href="/dashboard/account"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] transition-colors duration-150"
                  style={menuItemStyle}
                  onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF'; e.currentTarget.style.background = '#18181B' }}
                  onMouseLeave={e => { e.currentTarget.style.color = '#A1A1AA'; e.currentTarget.style.background = 'transparent' }}
                >
                  <Settings size={13} /> Настройки
                </Link>
                <div style={{ height: 1, background: '#232329', margin: '4px 0' }} />
                <button
                  onClick={logout}
                  className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] w-full transition-colors duration-150"
                  style={{ ...menuItemStyle, background: 'transparent', border: 'none', cursor: 'pointer' }}
                  onMouseEnter={e => { e.currentTarget.style.color = '#FCA5A5'; e.currentTarget.style.background = 'rgba(239,68,68,0.06)' }}
                  onMouseLeave={e => { e.currentTarget.style.color = '#A1A1AA'; e.currentTarget.style.background = 'transparent' }}
                >
                  <LogOut size={13} /> Выйти
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  )
}

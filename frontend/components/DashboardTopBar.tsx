'use client'

import { useEffect, useState, useRef } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { Settings, LogOut, ChevronDown, Home, ArrowLeft } from 'lucide-react'
import { NotificationBell } from '@/components/NotificationBell'
import { ThemeToggle } from '@/components/cabinet/ThemeToggle'
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
    color: 'var(--text-2)', textDecoration: 'none', display: 'flex',
    alignItems: 'center', gap: 8,
  }

  return (
    <header
      className="hidden sm:flex sticky top-0 z-30 items-center justify-between px-5 shrink-0"
      style={{
        height: 48,
        background: 'color-mix(in srgb, var(--bg) 90%, transparent)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid var(--line)',
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
              color: 'var(--text-3)', padding: '3px 5px', borderRadius: 5,
              transition: 'color 0.15s ease, background 0.15s ease',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-2)'; e.currentTarget.style.background = 'rgba(255,255,255,0.05)' }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-3)'; e.currentTarget.style.background = 'none' }}
          >
            <ArrowLeft size={13} />
          </button>
        )}

        <div className="flex items-center gap-1.5" style={{ fontSize: 12, letterSpacing: '0.02em' }}>
          <Link
            href="/"
            style={{ color: 'var(--text-3)', textDecoration: 'none', transition: 'color 0.15s ease', display: 'flex', alignItems: 'center' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-3)' }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-3)' }}
          >
            <Home size={11} />
          </Link>
          <span style={{ color: 'var(--line)' }}>/</span>
          <span style={{ color: 'var(--text-3)', fontWeight: 500, letterSpacing: '0.05em', fontSize: 11, textTransform: 'uppercase' }}>
            ПУЛЬТ
          </span>
          {crumb && crumb !== 'Пульт' && <>
            <span style={{ color: 'var(--line)' }}>/</span>
            <span style={{ color: 'var(--text-3)' }}>{crumb}</span>
          </>}
        </div>
      </div>

      {/* Right: theme + notifications + user */}
      <div className="flex items-center gap-2">
        <ThemeToggle />
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
                background: 'var(--surface-h)',
                border: '1px solid var(--line)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700, color: 'var(--violet-text)',
              }}>
                {user.name.charAt(0).toUpperCase()}
              </div>
              <span style={{
                fontSize: 12, color: 'var(--text-3)',
                maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
                className="hidden lg:block"
              >
                {user.name}
              </span>
              <ChevronDown size={11} style={{ color: 'var(--text-3)' }} />
            </button>

            {open && (
              <div
                className="absolute right-0 top-full mt-1.5 w-44 rounded-[8px] overflow-hidden z-50 py-1"
                style={{ background: 'var(--surface)', border: '1px solid var(--line)', boxShadow: '0 8px 24px rgba(0,0,0,0.5)' }}
              >
                <Link
                  href="/"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] transition-colors duration-150"
                  style={menuItemStyle}
                  onMouseEnter={e => { e.currentTarget.style.color = 'var(--text)'; e.currentTarget.style.background = 'var(--surface-h)' }}
                  onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-2)'; e.currentTarget.style.background = 'transparent' }}
                >
                  <Home size={13} /> На главную
                </Link>
                <Link
                  href="/dashboard/account"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] transition-colors duration-150"
                  style={menuItemStyle}
                  onMouseEnter={e => { e.currentTarget.style.color = 'var(--text)'; e.currentTarget.style.background = 'var(--surface-h)' }}
                  onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-2)'; e.currentTarget.style.background = 'transparent' }}
                >
                  <Settings size={13} /> Настройки
                </Link>
                <div style={{ height: 1, background: 'var(--line)', margin: '4px 0' }} />
                <button
                  onClick={logout}
                  className="flex items-center gap-2 px-3.5 py-2.5 text-[13px] w-full transition-colors duration-150"
                  style={{ ...menuItemStyle, background: 'transparent', border: 'none', cursor: 'pointer' }}
                  onMouseEnter={e => { e.currentTarget.style.color = 'var(--danger)'; e.currentTarget.style.background = 'rgba(239,68,68,0.06)' }}
                  onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-2)'; e.currentTarget.style.background = 'transparent' }}
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

'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Menu, X, LayoutDashboard, Package, TrendingUp, TrendingDown, Shield, Settings,
  Users, Gift, Sparkles,
} from 'lucide-react'
import { FLAGS } from '@/lib/featureFlags'

// ── ПУЛЬТ logo icon — precision monitoring / control center ──────────────────
function PultIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="10" cy="10" r="8.25" stroke="white" strokeWidth="1.5"/>
      <circle cx="10" cy="10" r="4" stroke="white" strokeWidth="1" strokeOpacity="0.45"/>
      <circle cx="10" cy="10" r="1.75" fill="white"/>
    </svg>
  )
}

// ── Nav structure ─────────────────────────────────────────────────────────────

// ── ПУЛЬТ V2 — каноническая навигация. Ровно 6 вкладок. Товар = атом.
// Линзы (Реклама/SEO/Отзывы/Цена) НЕ в меню — они фильтры внутри вкладок.
const NAV_SECTIONS = [
  {
    title: '',
    items: [
      { href: '/dashboard',               icon: LayoutDashboard, label: 'Кабинет Пульта',      desc: 'Главное за сегодня: где теряете и что делать' },
      { href: '/dashboard/products',       icon: Package,         label: 'Товары',              desc: 'Все товары. Внутри — всё о каждом' },
      { href: '/dashboard/opportunities',  icon: TrendingUp,      label: 'Возможности',         desc: 'Где заработать больше, в рублях' },
      { href: '/dashboard/leaks',          icon: TrendingDown,    label: 'Что съедает прибыль', desc: 'Куда уходят деньги: реклама, комиссия, возвраты' },
      { href: '/dashboard/risks',          icon: Shield,          label: 'Риски',               desc: 'Что грозит блокировкой или штрафом' },
      { href: '/dashboard/settings',       icon: Settings,        label: 'Настройки',           desc: 'Подключить WB / Ozon, уведомления' },
    ],
  },
  // GROWTH-контур — заморожен. Появляется ТОЛЬКО при NEXT_PUBLIC_GROWTH_CONTOUR=1.
  ...(FLAGS.growthContour ? [{
    title: 'Рост',
    items: [
      { href: '/dashboard/referrals', icon: Users,    label: 'Рефералы',   desc: 'Приглашайте — получайте бонусы' },
      { href: '/dashboard/deals',     icon: Gift,     label: 'Сделки',     desc: 'Спецпредложения для роста' },
      { href: '/community',           icon: Sparkles, label: 'Сообщество', desc: 'Селлеры, кейсы, обмен опытом' },
    ],
  }] : []),
] as const

// ── Nav item ──────────────────────────────────────────────────────────────────

function NavItem({ href, icon: Icon, label, desc, active, badge }: {
  href: string
  icon: React.ElementType
  label: string
  desc?: string
  active: boolean
  badge?: number
}) {
  return (
    <Link
      href={href}
      className="flex items-start gap-2.5 rounded-[10px] transition-all duration-150"
      style={{
        padding: '9px 10px',
        background: active ? 'var(--violet-dim)' : 'transparent',
        boxShadow: active ? 'inset 2px 0 0 var(--violet)' : 'none',
      }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--surface-h)' }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      <Icon size={16} style={{ flexShrink: 0, marginTop: 1, color: active ? 'var(--violet)' : 'var(--text-2)' }} />
      <span style={{ minWidth: 0 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600, lineHeight: 1.2, color: active ? 'var(--violet-text)' : 'var(--text)' }}>{label}</span>
          {badge != null && badge > 0 && (
            <span style={{
              minWidth: 16, height: 16, borderRadius: 8, background: 'var(--warning)', color: 'var(--bg)',
              fontSize: 9, fontWeight: 800, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: '0 4px',
            }}>{badge}</span>
          )}
        </span>
        {/* Описание вкладки — новичок понимает назначение без наведения */}
        {desc && <span style={{ display: 'block', fontSize: 11, color: 'var(--text-3)', lineHeight: 1.35, marginTop: 2 }}>{desc}</span>}
      </span>
    </Link>
  )
}

// ── Main sidebar ──────────────────────────────────────────────────────────────

export function Sidebar() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)

  // Initialize from localStorage synchronously on mount, then stay synced via event
  const [aeCount, setAeCount] = useState(0)
  useEffect(() => {
    // Initial read
    try {
      const n = parseInt(localStorage.getItem('ae_active_count') ?? '0')
      setAeCount(isNaN(n) ? 0 : n)
    } catch {}

    // Real-time updates from CopilotBar (no pathname-polling needed)
    const handler = (e: Event) => {
      const cnt = (e as CustomEvent<number>).detail
      if (typeof cnt === 'number') setAeCount(cnt)
    }
    window.addEventListener('ae-count-update', handler)
    return () => window.removeEventListener('ae-count-update', handler)
  }, [])

  useEffect(() => { setOpen(false) }, [pathname])

  function isActive(href: string) {
    if (href === '/dashboard') return pathname === '/dashboard'
    return pathname === href || pathname?.startsWith(href + '/')
  }

  const SidebarContent = () => (
    <div className="flex flex-col h-full" style={{
      background: 'var(--surface)',
      borderRight: '1px solid var(--line)',
    }}>

      {/* Logo */}
      <div className="px-4 pt-5 pb-5">
        <Link href="/" className="flex items-center gap-2.5 group" style={{ textDecoration: 'none' }}>
          <div style={{
            width: 30, height: 30,
            borderRadius: 8,
            background: 'var(--surface-h)',
            border: '1px solid var(--line)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
            transition: 'border-color 0.15s ease',
          }}>
            <PultIcon size={16} />
          </div>
          <div>
            <div style={{
              fontSize: 15, fontWeight: 700, color: 'var(--text)',
              letterSpacing: '-0.02em', lineHeight: 1,
            }}>
              ПУЛЬТ
            </div>
            <div style={{
              fontSize: 10, color: 'var(--text-3)',
              letterSpacing: '0.04em', marginTop: 2,
              lineHeight: 1,
            }}>
              MARKETPLACE OS
            </div>
          </div>
        </Link>
      </div>

      {/* System status indicator */}
      <div style={{
        margin: '0 12px 12px',
        padding: '7px 10px',
        borderRadius: 6,
        background: 'rgba(34,197,94,0.06)',
        border: '1px solid rgba(34,197,94,0.14)',
        display: 'flex', alignItems: 'center', gap: 7,
      }}>
        <span className="signal-dot signal-dot-green" />
        <span style={{ fontSize: 10.5, fontWeight: 600, color: '#4ADE80', letterSpacing: '0.06em' }}>
          МОНИТОРИНГ АКТИВЕН
        </span>
      </div>

      {/* Divider */}
      <div style={{ height: 1, background: 'var(--line)', margin: '0 0 8px' }} />

      {/* Nav */}
      <nav className="flex-1 flex flex-col px-2 py-1 overflow-y-auto gap-3">
        {NAV_SECTIONS.map((section, si) => (
          <div key={si}>
            {section.title && (
              <p style={{
                fontSize: 9.5, fontWeight: 700,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                color: 'var(--text-3)', paddingLeft: 12, paddingBottom: 4, paddingTop: 4,
              }}>
                {section.title}
              </p>
            )}
            <div className="flex flex-col gap-0.5">
              {section.items.map(({ href, icon, label, desc }) => (
                <NavItem
                  key={href} href={href} icon={icon} label={label} desc={desc}
                  active={isActive(href)}
                />
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Bottom — version */}
      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--line)',
      }}>
        <p style={{ fontSize: 10, color: 'var(--text-3)', letterSpacing: '0.04em' }}>
          ПУЛЬТ v2.0 · {new Date().getFullYear()}
        </p>
      </div>
    </div>
  )

  return (
    <>
      {/* Mobile topbar */}
      <header
        className="sticky top-0 z-40 sm:hidden flex items-center justify-between px-4 h-14"
        style={{ background: 'var(--surface)', borderBottom: '1px solid var(--line)' }}
      >
        <Link href="/" className="flex items-center gap-2" style={{ textDecoration: 'none' }}>
          <PultIcon size={16} />
          <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', letterSpacing: '-0.02em' }}>
            ПУЛЬТ
          </span>
        </Link>
        <button
          onClick={() => setOpen(v => !v)}
          style={{ color: 'var(--text-2)', background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}
        >
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </header>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-50 sm:hidden">
          <div
            className="absolute inset-0"
            style={{ background: 'rgba(0,0,0,0.7)' }}
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 top-0 h-full" style={{ width: 240 }}>
            <SidebarContent />
          </div>
        </div>
      )}

      {/* Desktop sidebar */}
      <aside
        data-sidebar
        className="hidden sm:block shrink-0"
        style={{ width: 220, height: '100vh', position: 'sticky', top: 0 }}
      >
        <SidebarContent />
      </aside>
    </>
  )
}

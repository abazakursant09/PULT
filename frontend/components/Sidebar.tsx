'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  Menu, X, LayoutDashboard, Calculator, Zap, FileText,
  Users, Building2, Truck, BarChart2, Megaphone, CreditCard, Upload, Target, TrendingUp, FlaskConical,
} from 'lucide-react'

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

const NAV_SECTIONS = [
  {
    title: '',  // L1 — Decision Layer (единственный ежедневный экран)
    items: [
      { href: '/dashboard',          icon: LayoutDashboard, label: 'Пульт' },
    ],
  },
  {
    title: 'Данные',  // L2 — Truth Layer (факты, без рекомендаций)
    items: [
      { href: '/dashboard/data',     icon: BarChart2,       label: 'Данные' },
    ],
  },
  {
    title: 'Инструменты',  // L3 — Execution Layer
    items: [
      { href: '/dashboard/seo',      icon: FileText,        label: 'SEO'          },
      { href: '/ad-strategy',        icon: Megaphone,       label: 'Реклама'      },
      { href: '/suppliers',          icon: Building2,       label: 'Поставщики'   },
      { href: '/logistics',          icon: Truck,           label: 'Логистика'    },
      { href: '/ai-agents',          icon: Zap,             label: 'AI'           },
      { href: '/dashboard/monitor',  icon: Target,          label: 'Мониторинг'   },
    ],
  },
  {
    title: 'Аккаунт',
    items: [
      { href: '/dashboard/billing',       icon: CreditCard,    label: 'Подписка'    },
      { href: '/dashboard/referrals',     icon: Users,         label: 'Рефералы'    },
      { href: '/dashboard/notifications', icon: Calculator,    label: 'Уведомления' },
    ],
  },
] as const

// ── Nav item ──────────────────────────────────────────────────────────────────

function NavItem({ href, icon: Icon, label, active, badge }: {
  href: string
  icon: React.ElementType
  label: string
  active: boolean
  badge?: number
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-2.5 px-3 py-2 rounded-[7px] transition-all duration-150"
      style={active ? {
        color: '#C4B5FD',
        background: 'rgba(124,58,237,0.12)',
        boxShadow: 'inset 2px 0 0 #7C3AED',
        fontWeight: 500,
      } : {
        color: '#52525B',
        fontWeight: 400,
      }}
      onMouseEnter={e => {
        if (!active) {
          e.currentTarget.style.color = '#A1A1AA'
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
        }
      }}
      onMouseLeave={e => {
        if (!active) {
          e.currentTarget.style.color = '#52525B'
          e.currentTarget.style.background = 'transparent'
        }
      }}
    >
      <Icon size={14} style={{ opacity: active ? 0.9 : 0.55, flexShrink: 0 }} />
      <span style={{ fontSize: 13, lineHeight: 1 }}>{label}</span>
      {badge != null && badge > 0 && (
        <span style={{
          marginLeft: 'auto', minWidth: 16, height: 16, borderRadius: 8,
          background: '#F59E0B', color: '#000',
          fontSize: 9, fontWeight: 800,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          padding: '0 4px', flexShrink: 0,
        }}>
          {badge}
        </span>
      )}
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
    if (href === '/dashboard') return pathname === '/dashboard' || pathname?.startsWith('/dashboard/products')
    return pathname === href || pathname?.startsWith(href + '/')
  }

  const SidebarContent = () => (
    <div className="flex flex-col h-full" style={{
      background: '#09090B',
      borderRight: '1px solid #232329',
    }}>

      {/* Logo */}
      <div className="px-4 pt-5 pb-5">
        <Link href="/" className="flex items-center gap-2.5 group" style={{ textDecoration: 'none' }}>
          <div style={{
            width: 30, height: 30,
            borderRadius: 8,
            background: '#111113',
            border: '1px solid #232329',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
            transition: 'border-color 0.15s ease',
          }}>
            <PultIcon size={16} />
          </div>
          <div>
            <div style={{
              fontSize: 15, fontWeight: 700, color: '#FFFFFF',
              letterSpacing: '-0.02em', lineHeight: 1,
            }}>
              ПУЛЬТ
            </div>
            <div style={{
              fontSize: 10, color: '#52525B',
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
      <div style={{ height: 1, background: '#232329', margin: '0 0 8px' }} />

      {/* Nav */}
      <nav className="flex-1 flex flex-col px-2 py-1 overflow-y-auto gap-3">
        {NAV_SECTIONS.map((section, si) => (
          <div key={si}>
            {section.title && (
              <p style={{
                fontSize: 9.5, fontWeight: 700,
                letterSpacing: '0.12em', textTransform: 'uppercase',
                color: '#3F3F46', paddingLeft: 12, paddingBottom: 4, paddingTop: 4,
              }}>
                {section.title}
              </p>
            )}
            <div className="flex flex-col gap-0.5">
              {section.items.map(({ href, icon, label }) => (
                <NavItem
                  key={href} href={href} icon={icon} label={label}
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
        borderTop: '1px solid #232329',
      }}>
        <p style={{ fontSize: 10, color: '#3F3F46', letterSpacing: '0.04em' }}>
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
        style={{ background: '#09090B', borderBottom: '1px solid #232329' }}
      >
        <Link href="/" className="flex items-center gap-2" style={{ textDecoration: 'none' }}>
          <PultIcon size={16} />
          <span style={{ fontSize: 15, fontWeight: 700, color: '#FFFFFF', letterSpacing: '-0.02em' }}>
            ПУЛЬТ
          </span>
        </Link>
        <button
          onClick={() => setOpen(v => !v)}
          style={{ color: '#71717A', background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}
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

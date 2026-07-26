'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { Home, Store, Upload, MessageSquare, Settings, User, Menu, X } from 'lucide-react'
import { useRouter } from 'next/navigation'

// ── Shell responsive state (P5) ───────────────────────────────────────────────
// On desktop the rail is a fixed 258px column and the drawer state is inert. Below ~1024px the
// rail becomes an off-canvas drawer that this context opens/closes — the hamburger (in SellerBar)
// and the drawer (Rail) live in different parts of the tree, so a small context is the seam that
// joins them. A default no-op value means SellerBar renders fine without a provider (e.g. in unit
// tests that mount a single page).
interface NavCtx { open: boolean; toggle: () => void; close: () => void }
const NavContext = createContext<NavCtx>({ open: false, toggle: () => {}, close: () => {} })

export function NavProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const toggle = useCallback(() => setOpen(o => !o), [])

  // Body scroll-lock + Escape-to-close while the drawer is open. Both are cleaned up on close so
  // desktop (where the drawer never opens) is never affected.
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = prev; window.removeEventListener('keydown', onKey) }
  }, [open])

  return <NavContext.Provider value={{ open, toggle, close }}>{children}</NavContext.Provider>
}

// The .s-app frame owns the open-state class (drives the drawer + scrim in CSS) and renders the
// backdrop. Kept as its own client component so app/dashboard/layout.tsx stays a thin server file.
export function ShellFrame({ children }: { children: React.ReactNode }) {
  const { open, close } = useContext(NavContext)
  return (
    <div className={`s-app${open ? ' nav-open' : ''}`}>
      {children}
      <button type="button" className="s-scrim" aria-hidden={!open} tabIndex={-1} onClick={close} />
    </div>
  )
}

// ── Rail (seller-native навигация) ────────────────────────────────────────────
interface NavItem { h: string; l: string; d: string }
// P7.2 — nav shows ONLY real, working Advisory-MVP surfaces (no fabricated counts, no mock
// cabinet). Today + Decision Feed live inside /dashboard itself.
const NAV: { g: string; items: NavItem[] }[] = [
  { g: 'Обзор', items: [{ h: '/dashboard', l: 'Главная', d: 'home' }] },
  { g: 'Данные', items: [
    // Stores come before the import on purpose: a CSV lands in ONE store (1.4.2), so the store is
    // the thing that must exist first. "Товары" is NOT listed — the global products page is still
    // a ComingSoon stub; a store's products live inside that store's page.
    { h: '/dashboard/stores', l: 'Магазины', d: 'store' },
    { h: '/dashboard/import', l: 'Импорт данных', d: 'import' },
    { h: '/dashboard/reviews', l: 'Отзывы', d: 'chat' },
    // "Мониторинг" is NOT listed — its updates are invented news, a lie until it reports
    // something real. The backend is left untouched.
  ] },
  // "Идеи" is NOT listed — it renders the legacy AppShell (removed monitoring beacon + "в
  // разработке" sections); six other pages share that shell, so the item is dropped from this nav.
  { g: 'Аккаунт', items: [
    // "Тариф" is NOT listed — its cards sell unshipped modules and reach a real YooKassa charge;
    // no seller-visible path may lead there until the commercial contents are approved.
    { h: '/dashboard/settings', l: 'Настройки', d: 'gear' },
    { h: '/dashboard/account', l: 'Аккаунт', d: 'user' },
  ] },
]
// lucide icons, consistent with the rest of the app (P4). Sized/coloured by the .s-nav CSS.
const ICON: Record<string, React.ReactNode> = {
  home:   <Home size={17} />,
  store:  <Store size={17} />,
  import: <Upload size={17} />,
  chat:   <MessageSquare size={17} />,
  gear:   <Settings size={17} />,
  user:   <User size={17} />,
}

export function Rail() {
  const path = usePathname()
  const { close } = useContext(NavContext)
  const active = (h: string) => h === '/dashboard' ? path === '/dashboard' : path?.startsWith(h)
  return (
    <aside className="s-rail">
      <div className="s-rail-top">
        <Link href="/dashboard" className="s-logo" onClick={close}>
          <span className="mk"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0A0B0D" strokeWidth="2.2"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.6" fill="#0A0B0D" stroke="none"/></svg></span>
          <b>ПУЛЬТ</b>
        </Link>
        <DrawerClose />
      </div>
      {NAV.map((sec, i) => (
        <div key={i}>
          {sec.g && <div className="s-glabel">{sec.g}</div>}
          {sec.items.map(it => (
            // close the drawer on navigation (mobile); a no-op on desktop where it is never open.
            // On the Executive Ledger store routes the active accent is green (PULT-LAUNCH-1.4.5I),
            // scoped to /dashboard/stores* only — every other section keeps the default accent.
            <Link key={it.h} href={it.h} onClick={close}
              className={`s-nav${active(it.h) ? ' on' : ''}${active(it.h) && it.h.startsWith('/dashboard/stores') ? ' s-nav--ledger' : ''}`}>
              <span className="s-nav-ic">{ICON[it.d]}</span>{it.l}
            </Link>
          ))}
        </div>
      ))}
      <Link href="/dashboard/account" onClick={close} className="s-foot s-clk">
        <span className="av">П</span><div><div className="nm">Мой кабинет</div><div className="pl">Бизнес-Пульт</div></div>
      </Link>
    </aside>
  )
}

export function SellerBar({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  const { toggle } = useContext(NavContext)
  return (
    <div className="s-bar">
      {/* hamburger — mobile only (hidden ≥1024 via CSS) */}
      <button type="button" className="s-burger" aria-label="Открыть меню" onClick={toggle}>
        <Menu size={18} />
      </button>
      <div><div className="ttl">{title}</div>{sub && <div className="sub">{sub}</div>}</div>
      <div className="sp" />
      {right}
    </div>
  )
}

// Drawer close affordance for inside the rail header on mobile (hidden on desktop via CSS).
export function DrawerClose() {
  const { close } = useContext(NavContext)
  return (
    <button type="button" className="s-drawer-x" aria-label="Закрыть меню" onClick={close}>
      <X size={18} />
    </button>
  )
}

export function useGo() { return useRouter() }

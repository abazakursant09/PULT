'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState } from 'react'
import { api } from '@/lib/api'

// ── Rail (seller-native навигация) ────────────────────────────────────────────
interface NavItem { h: string; l: string; d: string; c?: number; cr?: boolean }
const NAV: { g: string; items: NavItem[] }[] = [
  { g: 'Обзор', items: [{ h: '/dashboard', l: 'Главная', c: 5, cr: true, d: 'home' }] },
  { g: 'Товары', items: [
    { h: '/dashboard/products', l: 'Товары', c: 12, d: 'box' },
    { h: '/dashboard/sklad', l: 'Склад и поставки', c: 4, cr: true, d: 'stock' },
    { h: '/dashboard/zakazy', l: 'Заказы', c: 148, d: 'cart' },
  ] },
  { g: 'Деньги', items: [{ h: '/dashboard/finance', l: 'Финансы', d: 'money' }] },
  { g: 'Рост', items: [{ h: '/dashboard/opportunities', l: 'Продвижение', d: 'grow' }] },
  { g: 'Защита', items: [{ h: '/dashboard/risks', l: 'Риски', c: 2, cr: true, d: 'shield' }] },
  { g: '', items: [{ h: '/dashboard/settings', l: 'Настройки', d: 'gear' }] },
]
const ICON: Record<string, React.ReactNode> = {
  home: <><path d="M3 12 12 4l9 8"/><path d="M5 10v10h14V10"/></>,
  box: <><path d="M3 9 12 4l9 5v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></>,
  stock: <><path d="M21 8 12 3 3 8l9 5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></>,
  cart: <><circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.2 12.4a1.5 1.5 0 0 0 1.5 1.2h8.7a1.5 1.5 0 0 0 1.5-1.2L22 7H6"/></>,
  money: <><path d="M12 2v20"/><path d="M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></>,
  grow: <><path d="M4 18 10 11l4 4 6-7"/><path d="M15 6h5v5"/></>,
  shield: <><path d="M12 3 4 6v6c0 5 3.5 7.5 8 9 4.5-1.5 8-4 8-9V6z"/></>,
  gear: <><circle cx="12" cy="12" r="3"/><path d="M19.4 13a1.7 1.7 0 0 0 .3 1.9 2 2 0 1 1-2.8 2.8 1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2 2 2 0 1 1-2.8-2.8A1.7 1.7 0 0 0 4.6 13a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9 2 2 0 1 1 2.8-2.8A1.7 1.7 0 0 0 11.5 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2 2 2 0 1 1 2.8 2.8A1.7 1.7 0 0 0 19.4 11"/></>,
}

export function Rail() {
  const path = usePathname()
  const active = (h: string) => h === '/dashboard' ? path === '/dashboard' : path?.startsWith(h)
  return (
    <aside className="s-rail">
      <Link href="/dashboard" className="s-logo"><span className="mk"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0A0B0D" strokeWidth="2.2"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.6" fill="#0A0B0D" stroke="none"/></svg></span><b>ПУЛЬТ</b></Link>
      {NAV.map((sec, i) => (
        <div key={i}>
          {sec.g && <div className="s-glabel">{sec.g}</div>}
          {sec.items.map(it => (
            <Link key={it.h} href={it.h} className={`s-nav${active(it.h) ? ' on' : ''}`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">{ICON[it.d]}</svg>{it.l}
              {it.c != null && <span className={`c ${it.cr ? 'red' : 'mut'}`}>{it.c}</span>}
            </Link>
          ))}
        </div>
      ))}
      <Link href="/dashboard/settings" className="s-foot s-clk"><span className="av">С</span><div><div className="nm">Магазин «Дом и Кухня»</div><div className="pl">12 товаров · 3 МП</div></div></Link>
    </aside>
  )
}

export function SellerBar({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  const router = useRouter()
  const [q, setQ] = useState('')
  const go = () => { if (q.trim()) router.push(`/dashboard/products?q=${encodeURIComponent(q.trim())}`) }
  return (
    <div className="s-bar">
      <div><div className="ttl">{title}</div>{sub && <div className="sub">{sub}</div>}</div>
      <div className="sp" />
      <div className="s-search">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" onClick={go} style={{ cursor: 'pointer' }}><circle cx="11" cy="11" r="7"/><path d="m21 21-4-4"/></svg>
        <input placeholder="Поиск товара, артикула…" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') go() }} />
      </div>
      {right ?? <span className="s-daypill num">Сегодня · 148 заказов</span>}
    </div>
  )
}

// ── Действие (Проверить/Выполнить) — рабочее, ходит на api.actionEngine ────────
export function SellerAction({ insightKey }: { insightKey?: string }) {
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<string | null>(null)
  const [ok, setOk] = useState(false)

  async function run(dry: boolean) {
    if (!insightKey) { setRes('Действие выполняется вручную в карточке инструмента.'); setOk(false); return }
    setBusy(true)
    try {
      const r = await api.actionEngine.executeInsight(insightKey, { dry_run: dry })
      setOk(!dry && !!r.success)
      setRes(dry
        ? (r.status === 'dry_run_ok' ? 'Проверка пройдена — действие готово к выполнению.' : r.status === 'needs_input' ? 'Нужны данные кампании — откройте инструмент.' : (r.message || 'Проверка завершена.'))
        : (r.success ? 'Выполнено — изменение отправлено в кабинет маркетплейса.' : (r.message || 'Не удалось выполнить.')))
    } catch {
      setRes('Действие доступно при подключённом кабинете маркетплейса.'); setOk(false)
    } finally { setBusy(false) }
  }

  return (
    <div>
      <div className="s-rowact">
        <button className="s-btn gho" disabled={busy} onClick={() => run(true)}>Проверить</button>
        <button className="s-btn pri" disabled={busy} onClick={() => run(false)}>Выполнить</button>
        <span className="s-note">⚡ Пульт сделает сам</span>
      </div>
      {res && <div className="s-note" style={{ marginTop: 10, color: ok ? 'var(--gain)' : 'var(--tx-2)' }}>{res}</div>}
    </div>
  )
}

export function useGo() { return useRouter() }

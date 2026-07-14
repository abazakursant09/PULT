'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState } from 'react'
import { api } from '@/lib/api'

// ── Rail (seller-native навигация) ────────────────────────────────────────────
interface NavItem { h: string; l: string; d: string }
// P7.2 — nav shows ONLY real, working Advisory-MVP surfaces (no fabricated counts,
// no mock cabinet). Today + Decision Feed live inside /dashboard itself.
const NAV: { g: string; items: NavItem[] }[] = [
  { g: 'Обзор', items: [{ h: '/dashboard', l: 'Главная', d: 'home' }] },
  { g: 'Данные', items: [
    { h: '/dashboard/import', l: 'Импорт данных', d: 'import' },
    { h: '/dashboard/reviews', l: 'Отзывы', d: 'chat' },
    // "Мониторинг" is NOT listed. Its "Проверить обновления" button invites the seller to
    // "получить актуальные события с маркетплейсов", and what it returns is a random sample
    // from a hard-coded pool of invented news — WB commission hikes, a marking bill — three
    // of them flagged critical, stamped with fresh timestamps. Inventing market and legal
    // news for a seller who may act on it is not an unfinished feature, it is a lie, so the
    // page is unreachable until it reports something real. The backend is left untouched.
  ] },
  // "Идеи" is NOT listed. The page renders the legacy AppShell — the old Sidebar and top bar —
  // so one click out of this cleaned shell puts the seller in the previous cabinet: a green
  // "МОНИТОРИНГ АКТИВЕН" beacon for a contour that was removed for inventing news, and four
  // sections that are all "Раздел в разработке". Six other pages still render that shell, so
  // the item is dropped from this nav rather than the shared component being rewritten.
  { g: 'Аккаунт', items: [
    // "Тариф" is NOT listed. The tariff cards sell price monitoring, competitor analysis,
    // AI review replies and a "Финансовый модуль" — none of which the Advisory MVP delivers —
    // and the buy button reaches a real YooKassa payment. Until the commercial contents are
    // approved, no seller-visible path may lead to that charge. The page and the payment
    // backend are left untouched.
    { h: '/dashboard/settings', l: 'Настройки', d: 'gear' },
    { h: '/dashboard/account', l: 'Аккаунт', d: 'user' },
  ] },
]
const ICON: Record<string, React.ReactNode> = {
  home: <><path d="M3 12 12 4l9 8"/><path d="M5 10v10h14V10"/></>,
  import: <><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></>,
  pulse: <><path d="M3 12h4l3 8 4-16 3 8h4"/></>,
  bulb: <><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1h6c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></>,
  card: <><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/></>,
  chat: <><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></>,
  user: <><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></>,
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
            </Link>
          ))}
        </div>
      ))}
      <Link href="/dashboard/account" className="s-foot s-clk"><span className="av">П</span><div><div className="nm">Мой кабинет</div><div className="pl">Бизнес-Пульт</div></div></Link>
    </aside>
  )
}

export function SellerBar({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  // P7.2 — the mock product-search and the fabricated "148 заказов" pill are removed.
  return (
    <div className="s-bar">
      <div><div className="ttl">{title}</div>{sub && <div className="sub">{sub}</div>}</div>
      <div className="sp" />
      {right}
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

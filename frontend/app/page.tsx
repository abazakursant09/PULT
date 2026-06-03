'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  ArrowRight, ChevronDown, Plus, Minus,
  Upload, Search, MousePointerClick,
  ShieldCheck, FileText, Megaphone, Calculator, Eye, Zap, MessageSquare,
  Settings, LogOut, Home,
} from 'lucide-react'
import { NotificationBell } from '@/components/NotificationBell'
import { type User } from '@/lib/api'
import { clearSession } from '@/lib/session'
import { trackEvent, captureAttribution } from '@/lib/events'
import { LANDING_PROOF } from '@/config/landing-proof'

/* ─── Design tokens — single accent (violet), no gold ─────────────────────── */
// Tokens — single source of truth: styles/globals.css :root (no raw hex)
const R = {
  bg:     'var(--bg)',
  surf:   'var(--surface)',
  surfH:  'var(--surface-h)',
  text:   'var(--text)',
  text2:  'var(--text-2)',
  text3:  'var(--text-3)',
  line:   'var(--line)',
  accent: 'var(--violet)',       // action / the system's voice
  accentH:'var(--violet-h)',
  accentT:'var(--violet-text)',  // accent text on dark
  accentDim: 'var(--violet-dim)',
  gain:   'var(--success)',
  loss:   'var(--danger)',
  watch:  'var(--warning)',
}

function rub(n: number): string { return `${Math.round(n).toLocaleString('ru-RU')} ₽` }

/* ─── ПУЛЬТ logo mark — concentric target / control center ──────────────────── */
function PultIcon({ size = 18, color = '#FFFFFF' }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="10" cy="10" r="8.25" stroke={color} strokeWidth="1.5" />
      <circle cx="10" cy="10" r="4" stroke={color} strokeWidth="1" strokeOpacity="0.45" />
      <circle cx="10" cy="10" r="1.75" fill={color} />
    </svg>
  )
}

/* ─── Real product mock — L1 decision card (the screenshot) ─────────────────── */
function L1Mock({
  large = false,
  signalTitle = 'Реклама съедает «Кронштейн X»',
  signalLoss = 86_000,
  actionLabel = 'Снизить ставку',
}: {
  large?: boolean
  signalTitle?: string
  signalLoss?: number
  actionLabel?: string
}) {
  return (
    <div style={{
      background: R.surf, border: `1px solid ${R.line}`, borderRadius: 16,
      padding: large ? 26 : 22, width: '100%', maxWidth: large ? 560 : 460,
      boxShadow: '0 24px 60px rgba(0,0,0,0.45)',
    }}>
      {/* money strip */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 11, color: R.text3, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6 }}>
            Чистая прибыль · июнь
          </div>
          <div className="mono" style={{ fontSize: large ? 30 : 26, fontWeight: 800, color: R.gain, lineHeight: 1 }}>
            {rub(1_240_000)}
          </div>
        </div>
        <div className="mono" style={{ fontSize: 13, fontWeight: 700, color: R.gain }}>▲ +6%</div>
      </div>

      {/* signal card — the decision */}
      <div style={{
        background: R.bg, border: `1px solid ${R.line}`, borderLeft: `4px solid ${R.loss}`,
        borderRadius: 12, padding: large ? 22 : 18,
      }}>
        <div style={{ fontSize: 10.5, color: R.text3, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 10 }}>
          Главное сейчас
        </div>
        <div style={{ fontSize: large ? 17 : 15, fontWeight: 700, color: R.text, marginBottom: 6 }}>
          {signalTitle}
        </div>
        <div className="mono" style={{ fontSize: large ? 26 : 22, fontWeight: 800, color: R.loss, marginBottom: 16 }}>
          −{rub(signalLoss)} / мес
        </div>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13.5, fontWeight: 600,
          padding: '10px 18px', borderRadius: 9, background: R.accent, color: '#FFFFFF',
        }}>
          {actionLabel} <ArrowRight size={15} />
        </span>
      </div>
    </div>
  )
}

/* ─── Nav ─────────────────────────────────────────────────────────────────── */
function LandingNav({ user }: { user: User | null }) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onScroll() { setScrolled(window.scrollY > 8) }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
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

  return (
    <nav className="fixed top-0 inset-x-0 z-50" style={{
      height: 64,
      background: scrolled ? 'rgba(9,9,11,0.92)' : R.bg,
      borderBottom: `1px solid ${R.line}`,
      backdropFilter: scrolled ? 'blur(16px)' : 'none',
      WebkitBackdropFilter: scrolled ? 'blur(16px)' : 'none',
      transition: 'background 0.2s',
    }}>
      <div className="max-w-[1200px] mx-auto px-6 h-full flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5 shrink-0" style={{ textDecoration: 'none' }}>
          <div className="flex items-center justify-center" style={{
            width: 30, height: 30, borderRadius: 8, background: R.surf, border: `1px solid ${R.line}`,
          }}>
            <PultIcon size={16} />
          </div>
          <span style={{ fontSize: '1.0625rem', fontWeight: 700, letterSpacing: '-0.02em', color: R.text }}>ПУЛЬТ</span>
        </Link>

        <div className="hidden md:flex items-center gap-1">
          {([['#features', 'Возможности'], ['#pricing', 'Тарифы']] as [string, string][]).map(([href, label]) => (
            <a key={href} href={href}
              style={{ fontSize: '0.875rem', color: R.text2, padding: '6px 14px', borderRadius: 6, textDecoration: 'none', transition: 'color 0.15s' }}
              onMouseEnter={e => (e.currentTarget.style.color = R.text)}
              onMouseLeave={e => (e.currentTarget.style.color = R.text2)}>
              {label}
            </a>
          ))}
        </div>

        <div className="flex items-center gap-2">
          {user ? (
            <>
              <NotificationBell dropdownSide="down" />
              <div className="relative" ref={ref}>
                <button onClick={() => setOpen(v => !v)} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm"
                  style={{ color: R.text2, border: `1px solid ${open ? 'rgba(255,255,255,0.14)' : R.line}`, background: open ? R.surfH : 'transparent', cursor: 'pointer' }}>
                  <div className="flex items-center justify-center text-xs font-bold"
                    style={{ width: 24, height: 24, borderRadius: 7, background: R.surf, border: `1px solid ${R.line}`, color: R.accentT }}>
                    {user.name.charAt(0).toUpperCase()}
                  </div>
                  <span className="hidden lg:block" style={{ color: R.text, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user.name}</span>
                  <ChevronDown size={12} style={{ color: R.text2 }} />
                </button>
                {open && (
                  <div className="absolute right-0 top-full mt-1.5 w-44 rounded-[10px] overflow-hidden z-50" style={{ background: R.surf, border: `1px solid ${R.line}` }}>
                    <Link href="/dashboard" onClick={() => setOpen(false)} className="flex items-center gap-2.5 px-4 py-2.5 text-sm" style={{ color: R.text2, textDecoration: 'none' }}>
                      <Home size={14} /> Пульт
                    </Link>
                    <Link href="/dashboard/account" onClick={() => setOpen(false)} className="flex items-center gap-2.5 px-4 py-2.5 text-sm" style={{ color: R.text2, textDecoration: 'none' }}>
                      <Settings size={14} /> Настройки
                    </Link>
                    <button onClick={logout} className="flex items-center gap-2.5 px-4 py-2.5 text-sm w-full" style={{ color: R.text2, background: 'transparent', border: 'none', cursor: 'pointer' }}>
                      <LogOut size={14} /> Выйти
                    </button>
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <Link href="/login" className="btn btn-ghost" style={{ height: 38, padding: '0 16px', fontSize: '0.875rem', borderRadius: 6 }}>Войти</Link>
              <Link href="/register" className="btn btn-primary" style={{ height: 38, padding: '0 18px', fontSize: '0.875rem', borderRadius: 6 }}>Начать бесплатно</Link>
            </>
          )}
        </div>
      </div>
    </nav>
  )
}

/* ─── Section primitives ────────────────────────────────────────────────────── */
function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.10em', textTransform: 'uppercase', color: R.accentT, marginBottom: 14 }}>{children}</p>
}
function H2({ children }: { children: React.ReactNode }) {
  return <h2 style={{ fontSize: 'clamp(1.75rem, 3.5vw, 2.25rem)', fontWeight: 700, color: R.text, lineHeight: 1.15, letterSpacing: '-0.03em' }}>{children}</h2>
}

/* ─── FAQ item ──────────────────────────────────────────────────────────────── */
function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ background: R.surf, border: `1px solid ${R.line}`, borderRadius: 10, overflow: 'hidden' }}>
      <button onClick={() => setOpen(v => !v)} className="w-full flex items-center justify-between text-left"
        style={{ padding: '18px 22px', background: 'transparent', border: 'none', cursor: 'pointer' }}>
        <span style={{ fontSize: '0.9375rem', fontWeight: 600, color: R.text }}>{q}</span>
        {open ? <Minus size={16} style={{ color: R.text3, flexShrink: 0 }} /> : <Plus size={16} style={{ color: R.text3, flexShrink: 0 }} />}
      </button>
      {open && <p style={{ padding: '0 22px 18px', fontSize: '0.875rem', color: R.text2, lineHeight: 1.65 }}>{a}</p>}
    </div>
  )
}

/* ─── Page ────────────────────────────────────────────────────────────────── */
export default function LandingPage() {
  const [user, setUser] = useState<User | null>(null)

  useEffect(() => {
    const s = localStorage.getItem('user')
    if (s) try { setUser(JSON.parse(s)) } catch {}
  }, [])

  // Funnel: page view (once per mount) + per-section view (once per session)
  useEffect(() => {
    captureAttribution()                       // first-touch UTM/referrer
    trackEvent('landing_page_viewed', 'landing')
    const els = Array.from(document.querySelectorAll<HTMLElement>('[data-section]'))
    if (typeof IntersectionObserver === 'undefined' || els.length === 0) return
    const obs = new IntersectionObserver(entries => {
      for (const e of entries) {
        if (!e.isIntersecting) continue
        const name = (e.target as HTMLElement).dataset.section
        if (!name) { obs.unobserve(e.target); continue }
        try {
          if (sessionStorage.getItem(`bp_sv_${name}`)) { obs.unobserve(e.target); continue }
          sessionStorage.setItem(`bp_sv_${name}`, '1')
        } catch {}
        trackEvent('section_viewed', 'landing', name, { section: name })
        obs.unobserve(e.target)
      }
    }, { threshold: 0.3 })
    els.forEach(el => obs.observe(el))
    return () => obs.disconnect()
  }, [])

  const startHref = user ? '/dashboard' : '/register'

  // Proof / case visibility — never render literal placeholders
  const hasProofMetrics = !!(LANDING_PROOF.sellersCount || LANDING_PROOF.lossesFound)
  const hasCase = !!(LANDING_PROOF.caseAmount && LANDING_PROOF.caseQuote && LANDING_PROOF.caseAuthor)

  return (
    <div style={{ background: R.bg, minHeight: '100vh', color: R.text }}>
      <LandingNav user={user} />

      {/* ══ 1. HERO ══════════════════════════════════════════════════════════ */}
      <section style={{ paddingTop: 132, paddingBottom: 96 }}>
        <div className="max-w-[1200px] mx-auto px-8">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
            {/* Left */}
            <div>
              <p style={{ fontSize: '0.8125rem', fontWeight: 600, color: R.accentT, letterSpacing: '0.02em', marginBottom: 20 }}>
                Для селлеров Wildberries, Ozon и Яндекс Маркета
              </p>
              <h1 style={{ fontSize: 'clamp(2.25rem, 4.5vw, 3.25rem)', fontWeight: 800, lineHeight: 1.08, letterSpacing: '-0.035em', color: R.text, marginBottom: 24 }}>
                Вы теряете прибыль каждый день.<br />
                <span style={{ color: R.accentT }}>Пульт покажет где.</span>
              </h1>
              <p style={{ fontSize: '1.125rem', lineHeight: 1.65, color: R.text2, maxWidth: 480, marginBottom: 36 }}>
                Загрузите выгрузку Wildberries или Ozon — за 2 минуты увидите, сколько рублей утекает на рекламу, комиссии и убыточные товары. И что сделать сегодня.
              </p>
              <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-3 mb-4">
                <Link href={startHref} className="btn btn-primary w-full sm:w-auto"
                  onClick={() => trackEvent('landing_hero_cta_clicked', 'landing', undefined, { location: 'hero' })}
                  style={{ height: 52, padding: '0 28px', fontSize: '1rem', fontWeight: 600, borderRadius: 8, gap: 8 }}>
                  Найти мои потери <ArrowRight size={16} />
                </Link>
                <a href="#how"
                  onClick={() => trackEvent('landing_demo_clicked', 'landing')}
                  className="inline-flex items-center justify-center gap-1.5 self-center sm:self-auto"
                  style={{ fontSize: '0.9375rem', fontWeight: 500, color: R.accentT, textDecoration: 'none', padding: '8px 4px' }}>
                  Посмотреть, как это работает ↓
                </a>
              </div>
              <p style={{ fontSize: '0.8125rem', color: R.text3 }}>Без карты. 2 минуты до первого результата.</p>
            </div>
            {/* Right — real product (L1) */}
            <div className="flex justify-center lg:justify-end">
              <L1Mock />
            </div>
          </div>
        </div>
      </section>

      {/* ══ 2. PROOF STRIP ═══════════════════════════════════════════════════ */}
      <section data-section="proof" style={{ padding: '36px 0', background: R.surf, borderTop: `1px solid ${R.line}`, borderBottom: `1px solid ${R.line}` }}>
        <div className="max-w-[1200px] mx-auto px-8 flex flex-col items-center gap-6">
          <div className="flex flex-wrap items-center justify-center gap-10" style={{ opacity: 0.85 }}>
            <img src="/logos/wildberries.svg" alt="Wildberries" style={{ height: 30, objectFit: 'contain' }} />
            <img src="/logos/ozon.svg" alt="Ozon" style={{ height: 30, objectFit: 'contain' }} />
            <img src="/logos/yandex-market.svg" alt="Яндекс Маркет" style={{ height: 30, objectFit: 'contain' }} />
          </div>
          {hasProofMetrics && (
            <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-2" style={{ fontSize: '0.9375rem', color: R.text2 }}>
              {LANDING_PROOF.sellersCount && (
                <span><span className="mono" style={{ color: R.text, fontWeight: 700 }}>{LANDING_PROOF.sellersCount}</span> селлеров</span>
              )}
              {LANDING_PROOF.lossesFound && (
                <span><span className="mono" style={{ color: R.text, fontWeight: 700 }}>{LANDING_PROOF.lossesFound}</span> найденных потерь</span>
              )}
            </div>
          )}
        </div>
      </section>

      {/* ══ 3. PROBLEM ═══════════════════════════════════════════════════════ */}
      <section style={{ padding: '96px 0' }}>
        <div className="max-w-[1200px] mx-auto px-8">
          <div style={{ textAlign: 'center', marginBottom: 48 }}>
            <H2>Оборот растёт. А прибыль стоит. Знаете почему?</H2>
            <p style={{ fontSize: '1.0625rem', color: R.text2, marginTop: 14 }}>
              Деньги утекают там, где не видно в кабинете маркетплейса.
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-[840px] mx-auto">
            {[
              'Реклама съедает маржу — а вы видите только общий ДРР.',
              'Один убыточный товар тянет вниз весь отчёт.',
              'Комиссия и логистика незаметно съедают прибыль.',
              'Автоакции списывают деньги раньше, чем вы реагируете.',
            ].map((t, i) => (
              <div key={i} style={{ background: R.surf, border: `1px solid ${R.line}`, borderLeft: `3px solid ${R.loss}`, borderRadius: 10, padding: '20px 22px' }}>
                <p style={{ fontSize: '0.9375rem', color: R.text, lineHeight: 1.55 }}>{t}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══ 4. THE MOMENT ════════════════════════════════════════════════════ */}
      <section data-section="the_moment" style={{ padding: '96px 0', background: R.surf, borderTop: `1px solid ${R.line}`, borderBottom: `1px solid ${R.line}` }}>
        <div className="max-w-[1200px] mx-auto px-8">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
            <div>
              <Eyebrow>Вот что вы увидите</Eyebrow>
              <H2>Не отчёт. Одно найденное действие.</H2>
              <p style={{ fontSize: '1.0625rem', color: R.text2, lineHeight: 1.65, margin: '18px 0 32px', maxWidth: 460 }}>
                Пульт сам находит утечку, показывает сумму в рублях и говорит, что нажать. Вы не изучаете графики — вы делаете одно действие.
              </p>
              <Link href={startHref} className="btn btn-primary"
                onClick={() => trackEvent('landing_hero_cta_clicked', 'landing', undefined, { location: 'the_moment' })}
                style={{ height: 50, padding: '0 26px', fontSize: '0.9375rem', fontWeight: 600, borderRadius: 8, gap: 8 }}>
                Найти такие потери у себя <ArrowRight size={16} />
              </Link>
            </div>
            <div className="flex justify-center lg:justify-end">
              <L1Mock large signalTitle="Товар убыточен: «Чехол Y»" signalLoss={34_000} actionLabel="Поднять цену" />
            </div>
          </div>
        </div>
      </section>

      {/* ══ 5. HOW IT WORKS ══════════════════════════════════════════════════ */}
      <section id="how" style={{ padding: '96px 0' }}>
        <div className="max-w-[1200px] mx-auto px-8">
          <div style={{ textAlign: 'center', marginBottom: 56 }}>
            <Eyebrow>Как это работает</Eyebrow>
            <H2>Три шага. Две минуты.</H2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[
              { icon: Upload, num: '01', title: 'Загрузите выгрузку', desc: 'WB, Ozon, Яндекс Маркет. Только публичные данные.', badge: '2 минуты' },
              { icon: Search, num: '02', title: 'Пульт находит потери', desc: 'Считает реальную прибыль по каждому товару — в рублях.' },
              { icon: MousePointerClick, num: '03', title: 'Сделайте одно действие', desc: 'Снизить ставку, поднять цену, убрать товар.' },
            ].map(({ icon: Icon, num, title, desc, badge }, i) => (
              <div key={i} style={{ background: R.surf, border: `1px solid ${R.line}`, borderRadius: 10, padding: '32px' }}>
                <div className="flex items-center gap-3" style={{ marginBottom: 20 }}>
                  <div className="flex items-center justify-center" style={{ width: 40, height: 40, borderRadius: 10, background: R.accentDim }}>
                    <Icon size={18} style={{ color: R.accentT }} />
                  </div>
                  <span style={{ fontSize: '0.6875rem', fontWeight: 700, color: R.accentT, letterSpacing: '0.08em' }}>{num}</span>
                  {badge && (
                    <span style={{ marginLeft: 'auto', fontSize: '0.6875rem', fontWeight: 600, color: R.gain, background: 'rgba(52,211,153,0.10)', border: '1px solid rgba(52,211,153,0.25)', borderRadius: 6, padding: '3px 8px' }}>
                      {badge}
                    </span>
                  )}
                </div>
                <h3 style={{ fontSize: '1.0625rem', fontWeight: 700, color: R.text, marginBottom: 10 }}>{title}</h3>
                <p style={{ fontSize: '0.9375rem', color: R.text2, lineHeight: 1.65 }}>{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══ 6. CASE STUDY ════════════════════════════════════════════════════ */}
      {hasCase && (
        <section style={{ padding: '96px 0', background: R.surf, borderTop: `1px solid ${R.line}`, borderBottom: `1px solid ${R.line}` }}>
          <div className="max-w-[760px] mx-auto px-8 text-center">
            <div className="mono" style={{ fontSize: 'clamp(2rem, 4vw, 2.75rem)', fontWeight: 800, color: R.gain, letterSpacing: '-0.02em', marginBottom: 8 }}>
              {LANDING_PROOF.caseAmount}
            </div>
            <p style={{ fontSize: '0.8125rem', color: R.text3, letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 28 }}>за две недели</p>
            <p style={{ fontSize: '1.25rem', lineHeight: 1.6, color: R.text, marginBottom: 20 }}>
              {LANDING_PROOF.caseQuote}
            </p>
            <p style={{ fontSize: '0.875rem', color: R.text2 }}>— {LANDING_PROOF.caseAuthor}</p>
          </div>
        </section>
      )}

      {/* ══ 7. SECURITY ══════════════════════════════════════════════════════ */}
      <section style={{ padding: '80px 0' }}>
        <div className="max-w-[1000px] mx-auto px-8">
          <div style={{ background: R.surf, border: `1px solid ${R.line}`, borderRadius: 14, padding: '36px 40px' }}>
            <div className="flex items-start gap-4">
              <div className="flex items-center justify-center shrink-0" style={{ width: 44, height: 44, borderRadius: 10, background: R.accentDim }}>
                <ShieldCheck size={22} style={{ color: R.accentT }} />
              </div>
              <div>
                <h3 style={{ fontSize: '1.125rem', fontWeight: 700, color: R.text, marginBottom: 14 }}>Ваши данные остаются вашими</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-10 gap-y-3">
                  {['Только публичные данные', 'API-ключи не нужны', 'Соответствие 152-ФЗ', 'Данные не передаются третьим лицам'].map((t, i) => (
                    <div key={i} className="flex items-center gap-2.5" style={{ fontSize: '0.9375rem', color: R.text2 }}>
                      <span style={{ width: 5, height: 5, borderRadius: '50%', background: R.gain, flexShrink: 0 }} />
                      {t}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── CTA after Security (close the gap to Pricing) ── */}
      <section style={{ padding: '0 32px 56px', textAlign: 'center' }}>
        <Link href={startHref} className="btn btn-primary"
          onClick={() => trackEvent('landing_hero_cta_clicked', 'landing', undefined, { location: 'after_security' })}
          style={{ display: 'inline-flex', height: 52, padding: '0 28px', fontSize: '1rem', fontWeight: 600, borderRadius: 8, gap: 8 }}>
          Найти мои потери <ArrowRight size={16} />
        </Link>
      </section>

      {/* ══ 8. FEATURES ══════════════════════════════════════════════════════ */}
      <section id="features" style={{ padding: '80px 0' }}>
        <div className="max-w-[1200px] mx-auto px-8">
          <div style={{ textAlign: 'center', marginBottom: 40 }}>
            <H2>И ещё 6 инструментов внутри</H2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 max-w-[920px] mx-auto">
            {[
              { icon: FileText, title: 'SEO-карточки' },
              { icon: MessageSquare, title: 'Автоответы' },
              { icon: Megaphone, title: 'Реклама' },
              { icon: Calculator, title: 'Калькулятор прибыли' },
              { icon: Eye, title: 'Разведка конкурентов' },
              { icon: Zap, title: 'Автоакции' },
            ].map(({ icon: Icon, title }, i) => (
              <div key={i} style={{ background: R.surf, border: `1px solid ${R.line}`, borderRadius: 10, padding: '20px 22px', display: 'flex', alignItems: 'center', gap: 14 }}>
                <div className="flex items-center justify-center shrink-0" style={{ width: 36, height: 36, borderRadius: 9, background: R.accentDim }}>
                  <Icon size={17} style={{ color: R.accentT }} />
                </div>
                <p style={{ fontSize: '0.9375rem', fontWeight: 600, color: R.text }}>{title}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ══ 9. PRICING ═══════════════════════════════════════════════════════ */}
      <section id="pricing" data-section="pricing" style={{ padding: '96px 0', background: R.surf, borderTop: `1px solid ${R.line}`, borderBottom: `1px solid ${R.line}` }}>
        <div className="max-w-[1200px] mx-auto px-8">
          <div style={{ textAlign: 'center', marginBottom: 12 }}>
            <H2>Окупается первой найденной утечкой</H2>
            <p style={{ fontSize: '1.0625rem', color: R.text2, marginTop: 14, marginBottom: 40 }}>14 дней бесплатно. Без карты.</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 max-w-[780px] mx-auto">
            {/* Базовый */}
            <div style={{ padding: '36px 32px', borderRadius: 12, background: R.bg, border: `1px solid ${R.line}`, display: 'flex', flexDirection: 'column' }}>
              <h3 style={{ fontSize: '1.375rem', fontWeight: 800, color: R.text, letterSpacing: '-0.02em', marginBottom: 8 }}>Базовый</h3>
              <div style={{ marginBottom: 20 }}>
                <span className="mono" style={{ fontSize: '2.25rem', fontWeight: 800, color: R.text, letterSpacing: '-0.03em' }}>1 990 ₽</span>
                <span style={{ fontSize: '0.9375rem', color: R.text2 }}>/мес</span>
              </div>
              <p style={{ fontSize: '0.875rem', color: R.text2, marginBottom: 28, lineHeight: 1.6 }}>
                1 маркетплейс. Поиск потерь, решение дня, калькулятор прибыли.
              </p>
              <div style={{ flex: 1 }} />
              <Link href="/register" className="btn btn-ghost w-full" style={{ justifyContent: 'center', height: 46, borderRadius: 8 }}>
                Начать бесплатно
              </Link>
            </div>
            {/* Профи */}
            <div style={{ padding: '36px 32px', borderRadius: 12, background: R.bg, border: `1px solid rgba(124,58,237,0.40)`, display: 'flex', flexDirection: 'column' }}>
              <div className="inline-flex self-start items-center px-3 py-1 rounded-full mb-5" style={{ background: R.accent, color: '#fff', fontSize: '0.6875rem', fontWeight: 700, letterSpacing: '0.04em' }}>
                Популярный
              </div>
              <h3 style={{ fontSize: '1.375rem', fontWeight: 800, color: R.text, letterSpacing: '-0.02em', marginBottom: 8 }}>Профи</h3>
              <div style={{ marginBottom: 20 }}>
                <span className="mono" style={{ fontSize: '2.25rem', fontWeight: 800, color: R.text, letterSpacing: '-0.03em' }}>4 990 ₽</span>
                <span style={{ fontSize: '0.9375rem', color: R.text2 }}>/мес</span>
              </div>
              <p style={{ fontSize: '0.875rem', color: R.text2, marginBottom: 28, lineHeight: 1.6 }}>
                3 маркетплейса. Всё из Базового + разведка конкурентов, автоакции, SEO, ИИ-агенты.
              </p>
              <div style={{ flex: 1 }} />
              <Link href="/register" className="btn btn-primary w-full" style={{ justifyContent: 'center', height: 46, borderRadius: 8 }}>
                Начать бесплатно
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ══ 10. FAQ ══════════════════════════════════════════════════════════ */}
      <section data-section="faq" style={{ padding: '96px 0' }}>
        <div className="max-w-[760px] mx-auto px-8">
          <div style={{ textAlign: 'center', marginBottom: 48 }}>
            <H2>Коротко о главном</H2>
          </div>
          <div className="flex flex-col gap-3">
            <FaqItem q="Безопасны ли мои данные?" a="Да. Пульт работает только с публичными данными, соответствует 152-ФЗ и не передаёт данные третьим лицам." />
            <FaqItem q="Нужны ли API-ключи?" a="Нет. Достаточно загрузить выгрузку из кабинета маркетплейса — ключи не требуются." />
            <FaqItem q="Чем отличается от аналитики?" a="Аналитика показывает цифры и графики. Пульт говорит, где вы теряете деньги и что сделать сегодня — одно конкретное действие." />
            <FaqItem q="Что после 14 дней?" a="Выбираете тариф или останавливаете — без автоматических списаний." />
            <FaqItem q="Нужна ли карта?" a="Нет. Регистрация и пробный период — без карты." />
          </div>
        </div>
      </section>

      {/* ══ 11. FINAL CTA ════════════════════════════════════════════════════ */}
      <section style={{ padding: '112px 32px', textAlign: 'center' }}>
        <div className="max-w-[600px] mx-auto">
          <h2 style={{ fontSize: 'clamp(1.875rem, 4vw, 2.5rem)', fontWeight: 800, color: R.text, letterSpacing: '-0.035em', lineHeight: 1.15, marginBottom: 16 }}>
            Сколько вы теряете прямо сейчас?
          </h2>
          <p style={{ fontSize: '1.125rem', color: R.text2, marginBottom: 36 }}>Узнайте за 2 минуты. Без карты.</p>
          <Link href={startHref} className="btn btn-primary"
            onClick={() => trackEvent('landing_hero_cta_clicked', 'landing', undefined, { location: 'final' })}
            style={{ height: 56, padding: '0 40px', fontSize: '1.0625rem', fontWeight: 700, borderRadius: 10, gap: 10 }}>
            Найти мои потери <ArrowRight size={18} />
          </Link>
        </div>
      </section>

      {/* ══ FOOTER ═══════════════════════════════════════════════════════════ */}
      <footer style={{ background: R.surf, padding: '56px 32px 40px', borderTop: `1px solid ${R.line}` }}>
        <div className="max-w-[1200px] mx-auto">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-8 mb-10">
            <div>
              <div className="flex items-center gap-2.5 mb-3">
                <PultIcon size={18} />
                <span style={{ fontSize: '1rem', fontWeight: 700, letterSpacing: '-0.02em', color: R.text }}>ПУЛЬТ</span>
              </div>
              <p style={{ fontSize: '0.875rem', color: R.text3, maxWidth: 320, lineHeight: 1.6 }}>
                Находит, где вы теряете прибыль на Wildberries, Ozon и Яндекс Маркете.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              {([['#features', 'Возможности'], ['#pricing', 'Тарифы'], ['/login', 'Войти']] as [string, string][]).map(([href, label]) => (
                <a key={href} href={href} style={{ fontSize: '0.875rem', color: R.text2, textDecoration: 'none' }}>{label}</a>
              ))}
            </div>
          </div>
          <div style={{ height: 1, background: R.line, marginBottom: 24 }} />
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            <p style={{ fontSize: '0.75rem', color: R.text3 }}>© 2026 ПУЛЬТ</p>
            <div className="flex items-center gap-6">
              {([['/privacy', 'Политика'], ['/offer', 'Оферта'], ['/support', 'Контакты']] as [string, string][]).map(([href, label]) => (
                <Link key={href} href={href} style={{ fontSize: '0.75rem', color: R.text3, textDecoration: 'none' }}>{label}</Link>
              ))}
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}

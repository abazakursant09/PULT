'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { AlertTriangle, TrendingUp, TrendingDown, ArrowRight, FlaskConical, ChevronDown, Upload } from 'lucide-react'
import { api, type InsightItem, type FinanceSummaryItem } from '@/lib/api'
import { useData } from '@/hooks/useData'
import { trackEvent, stampFunnel, elapsedSince, firstTimeOnly, FUNNEL_TS } from '@/lib/events'
import { selectL1, splitL1, type L1ProductLine } from '@/lib/pultDecision'

// ── L1 ПУЛЬТ — V5 decision-first OS ──────────────────────────────────────────
// "1 screen = 1 decision". DEFAULT shows only: money strip + ONE problem + ONE
// CTA. All trust depth (why / mechanism / confidence / evidence / competing /
// leaks / gains) lives behind an EXPAND toggle. Decision faster than reading.

// Tokens — single source of truth: styles/globals.css :root (no raw hex)
const BG = 'var(--bg)'
const CARD = 'var(--surface)'
const BORDER = 'var(--line)'
const RED = 'var(--danger)'
const GREEN = 'var(--success)'
const AMBER = 'var(--warning)'
const MUTED = 'var(--text-3)'
const ACCENT_BG = 'var(--violet)'

const CONF_COLOR: Record<'high' | 'medium' | 'low', string> = { high: GREEN, medium: AMBER, low: MUTED }
const CONF_LABEL: Record<'high' | 'medium' | 'low', string> = { high: 'высокое', medium: 'среднее', low: 'низкое' }

function rub(n: number): string { return `${Math.round(n).toLocaleString('ru-RU')} ₽` }

function ProductRow({ p, color }: { p: L1ProductLine; color: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
                  background: CARD, border: `1px solid ${BORDER}`, borderRadius: 10, padding: '11px 14px' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.product}</div>
        <div style={{ fontSize: 11.5, color: MUTED }}>маржа <span style={{ color }}>{p.margin.toFixed(1)}%</span></div>
      </div>
      <div style={{ fontSize: 14, fontWeight: 800, color, whiteSpace: 'nowrap' }}>
        {p.effect_rub >= 0 ? '+' : '−'}{rub(Math.abs(p.effect_rub))}
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const router = useRouter()
  const [expanded, setExpanded] = useState(false)   // progressive disclosure
  useEffect(() => {
    if (!localStorage.getItem('token')) { router.push('/login'); return }
    trackEvent('dashboard_opened', 'dashboard')
  }, [router])

  const { data: insightsData } = useData('/api/insights', () => api.actionEngine.getInsights(), 30_000)
  const { data: finance }      = useData('/api/finance/summary', () => api.finance.summary(), 60_000)

  const insights: InsightItem[] = insightsData?.insights ?? []
  const fin: FinanceSummaryItem[] = finance ?? []
  // has_data unknown while loading → treat as data present (no onboarding flicker)
  const hasData = insightsData?.has_data ?? true

  const decision = selectL1(insights, fin, hasData)
  const { default: d, expanded: ex } = splitL1(decision)
  const demo = d.state.mode === 'demo'
  const noData = d.state.mode === 'no_data'

  // first_insight_shown — fired once ever, classified by insight_type, with activation timing
  useEffect(() => {
    if (noData || !decision.problem) return
    if (!firstTimeOnly('bp_evt_first_insight')) return
    stampFunnel(FUNNEL_TS.firstInsight)
    trackEvent('first_insight_shown', 'dashboard', undefined, {
      insight_type: decision.problem.insight_type,
      time_to_first_insight_ms: elapsedSince(FUNNEL_TS.signup),
    })
  }, [noData, decision.problem?.insight_type])

  function toggleExpand() {
    setExpanded(v => { if (!v) trackEvent('l1_expand', 'dashboard'); return !v })
  }

  return (
    <div style={{ background: BG, minHeight: '100vh', padding: '28px 24px', maxWidth: 1020, margin: '0 auto' }}>
      {/* DEMO SAFETY — never present demo as the seller's real money */}
      {demo && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(251,191,36,0.12)', border: '1px solid rgba(251,191,36,0.35)', borderRadius: 10, padding: '10px 14px', marginBottom: 18 }}>
          <FlaskConical size={15} color={AMBER} />
          <span style={{ fontSize: 12.5, color: AMBER, fontWeight: 600 }}>Демо-режим — пример, не ваши данные.</span>
        </div>
      )}

      {/* NO DATA — onboarding, not fake numbers (Step 3) */}
      {noData && (
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderRadius: 14, padding: 28, textAlign: 'center' }}>
          <Upload size={26} color={MUTED} style={{ marginBottom: 12 }} />
          <div style={{ fontSize: 19, fontWeight: 800, color: 'var(--text)', marginBottom: 6 }}>Данных пока нет</div>
          <div style={{ fontSize: 13, color: MUTED, marginBottom: 18, maxWidth: 420, marginLeft: 'auto', marginRight: 'auto' }}>
            Загрузите выгрузку с маркетплейса (финансы и товары) — Пульт покажет вашу прибыль, потери и что делать.
          </div>
          <Link href="/dashboard/import"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13.5, fontWeight: 700,
                     padding: '11px 20px', borderRadius: 9, textDecoration: 'none', background: ACCENT_BG,
                     border: '1px solid rgba(124,58,237,0.5)', color: 'var(--text)' }}>
            Загрузить данные<ArrowRight size={15} />
          </Link>
        </div>
      )}

      {!noData && (<>
      {/* 1 — MONEY STRIP (net primary, no overload) */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 28, alignItems: 'stretch' }}>
        <div style={{ flex: 2, minWidth: 220, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 14, padding: 20 }}>
          <div style={{ fontSize: 12, color: MUTED, marginBottom: 6 }}>Чистая прибыль</div>
          <div style={{ fontSize: 34, fontWeight: 800, color: d.money_strip.net_profit >= 0 ? GREEN : RED }}>{rub(d.money_strip.net_profit)}</div>
        </div>
        <div style={{ flex: 1, minWidth: 140, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 14, padding: 20 }}>
          <div style={{ fontSize: 12, color: MUTED, marginBottom: 6 }}>Выручка</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--text)' }}>{rub(d.money_strip.revenue)}</div>
        </div>
        <div style={{ flex: 1, minWidth: 120, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 14, padding: 20 }}>
          <div style={{ fontSize: 12, color: MUTED, marginBottom: 6 }}>Маржа</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: d.money_strip.margin >= 0 ? GREEN : RED }}>{d.money_strip.margin.toFixed(1)}%</div>
        </div>
      </div>

      {/* 2 — ONE DOMINANT PROBLEM + ONE PRIMARY ACTION (decision in <5s) */}
      {d.problem ? (
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderLeft: `4px solid ${RED}`, borderRadius: 14, padding: 22, marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <AlertTriangle size={18} color={RED} />
            <span style={{ fontSize: 12, color: MUTED, textTransform: 'uppercase', letterSpacing: 0.5 }}>Главное сейчас</span>
          </div>
          <div style={{ fontSize: 19, fontWeight: 800, color: 'var(--text)', marginBottom: 6 }}>{d.problem.title}</div>
          {d.problem.impact_rub > 0 && <div style={{ fontSize: 16, fontWeight: 700, color: AMBER, marginBottom: 16 }}>−{rub(d.problem.impact_rub)} в месяц</div>}

          {/* ONE primary CTA only */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            {d.problem.primary_action && (
              <Link href={d.problem.primary_action.url}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13.5, fontWeight: 700,
                         padding: '11px 20px', borderRadius: 9, textDecoration: 'none', background: ACCENT_BG,
                         border: '1px solid rgba(124,58,237,0.5)', color: 'var(--text)' }}>
                {d.problem.primary_action.label}<ArrowRight size={15} />
              </Link>
            )}
            {/* EXPAND trigger — depth on demand, not on screen */}
            {ex && (
              <button onClick={toggleExpand}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5, background: 'none', border: 'none',
                         color: MUTED, fontSize: 12.5, cursor: 'pointer', padding: '6px 2px' }}>
                {expanded ? 'Скрыть' : 'Почему это и как'}
                <ChevronDown size={14} style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} />
              </button>
            )}
          </div>

          {/* ── L1 EXPAND — trust depth (hidden by default) ── */}
          {expanded && ex && (
            <div style={{ marginTop: 18, paddingTop: 18, borderTop: `1px solid ${BORDER}`, display: 'grid', gap: 14 }}>
              {/* confidence */}
              <div>
                <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, padding: '3px 8px', borderRadius: 6,
                               color: CONF_COLOR[ex.confidence.level], background: `${CONF_COLOR[ex.confidence.level]}22`, border: `1px solid ${CONF_COLOR[ex.confidence.level]}55` }}>
                  Доверие: {CONF_LABEL[ex.confidence.level]}
                </span>
                <span style={{ fontSize: 11.5, color: MUTED, marginLeft: 8 }}>{ex.confidence.reason}</span>
              </div>
              {/* causal mechanism */}
              {ex.reason && <div style={{ fontSize: 13, color: 'var(--text-2)' }}>{ex.reason}</div>}
              <div>
                <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginBottom: 6 }}>{ex.causal_mechanism.description}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  {ex.causal_mechanism.chain.map((step, i) => (
                    <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: MUTED }}>
                      <span style={{ padding: '2px 7px', borderRadius: 5, background: 'rgba(255,255,255,0.05)', border: `1px solid ${BORDER}` }}>{step}</span>
                      {i < ex.causal_mechanism.chain.length - 1 && <span style={{ color: 'var(--text-3)' }}>→</span>}
                    </span>
                  ))}
                </div>
              </div>
              {/* selection + evidence */}
              <div style={{ fontSize: 11.5, color: MUTED }}>Почему это: {ex.selection_reason}</div>
              {ex.competing.length > 0 && (
                <div style={{ fontSize: 11.5, color: MUTED }}>Отложено: {ex.competing.map(c => `${c.title} (−${rub(c.impact_rub)})`).join(' · ')}</div>
              )}
              <div style={{ fontSize: 11.5, color: MUTED }}>Откуда: {ex.evidence.source} · {ex.evidence.volume} · {ex.evidence.period}</div>
              {/* secondary actions with full detail */}
              {ex.secondary_actions.length > 0 && (
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {ex.secondary_actions.map((a, i) => (
                    <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4, maxWidth: 260 }}>
                      <Link href={a.url} style={{ display: 'inline-flex', alignSelf: 'flex-start', fontSize: 12.5, fontWeight: 700,
                               padding: '8px 14px', borderRadius: 8, textDecoration: 'none', background: 'transparent', border: `1px solid ${BORDER}`, color: 'var(--text-2)' }}>
                        {a.label}
                      </Link>
                      {a.mechanism && <span style={{ fontSize: 10.5, color: 'var(--text-3)', lineHeight: 1.4 }}>{a.mechanism}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div style={{ background: CARD, border: `1px solid ${BORDER}`, borderLeft: `4px solid ${GREEN}`, borderRadius: 14, padding: 22, marginBottom: 20 }}>
          <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--text)' }}>Срочных проблем нет</div>
          <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>Деньги под контролем.</div>
        </div>
      )}

      {/* 3 — LEAKS / GAINS — hidden behind a single disclosure (no competition with the decision) */}
      {ex && (ex.leaks.length > 0 || ex.gains.length > 0) && <ProductBreakdown leaks={ex.leaks} gains={ex.gains} />}
      </>)}
    </div>
  )
}

function ProductBreakdown({ leaks, gains }: { leaks: L1ProductLine[]; gains: L1ProductLine[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button onClick={() => { if (!open) trackEvent('l1_breakdown', 'dashboard'); setOpen(v => !v) }}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', color: MUTED, fontSize: 12.5, cursor: 'pointer', padding: '4px 2px' }}>
        {open ? 'Скрыть разбор по товарам' : 'Разбор по товарам'}
        <ChevronDown size={14} style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} />
      </button>
      {open && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20, marginTop: 14 }}>
          <section id="risks">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <TrendingDown size={16} color={RED} />
              <h2 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>Под риском</h2>
            </div>
            <div style={{ fontSize: 11, color: MUTED, marginBottom: 12 }}>убыток или маржа ниже 10%</div>
            <div style={{ display: 'grid', gap: 8 }}>
              {leaks.length > 0 ? leaks.map(p => <ProductRow key={p.product} p={p} color={p.effect_rub < 0 ? RED : AMBER} />)
                : <div style={{ fontSize: 13, color: MUTED, padding: 12, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 10 }}>Товаров под риском нет.</div>}
            </div>
          </section>
          <section id="growth">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <TrendingUp size={16} color={GREEN} />
              <h2 style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>Приносит прибыль</h2>
            </div>
            <div style={{ fontSize: 11, color: MUTED, marginBottom: 12 }}>прибыль и здоровая маржа</div>
            <div style={{ display: 'grid', gap: 8 }}>
              {gains.length > 0 ? gains.map(p => <ProductRow key={p.product} p={p} color={GREEN} />)
                : <div style={{ fontSize: 13, color: MUTED, padding: 12, background: CARD, border: `1px solid ${BORDER}`, borderRadius: 10 }}>Пока нет прибыльных товаров.</div>}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

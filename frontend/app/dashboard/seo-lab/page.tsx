'use client'

import React, { useState, useEffect, useMemo } from 'react'
import { Brain, BarChart2, Zap, Loader2, Scale } from 'lucide-react'
import {
  api,
  type CreativeScoreResponse,
  type CreativeVariantItem,
  type CreativeBenchmarksResponse,
  type CreativeMarketplaceCompare,
  type CreativeIssue,
} from '@/lib/api'
import { scoreCreative, scoreForBothMarketplaces } from '@/lib/creative-scorer'

// ── Constants ─────────────────────────────────────────────────────────────────

const CATEGORIES = [
  { value: 'auto',        label: 'Авто' },
  { value: 'beauty',      label: 'Красота' },
  { value: 'home',        label: 'Дом' },
  { value: 'electronics', label: 'Электроника' },
  { value: 'clothes',     label: 'Одежда' },
  { value: 'sport',       label: 'Спорт' },
]

const PRESETS = [
  { value: 'premium',     label: 'Premium' },
  { value: 'sale',        label: 'Sale' },
  { value: 'minimal',     label: 'Minimal' },
  { value: 'tech',        label: 'Tech' },
  { value: 'beauty',      label: 'Beauty' },
  { value: 'marketplace', label: 'Market' },
  { value: 'luxury',      label: 'Luxury' },
  { value: 'wb-style',    label: 'WB' },
  { value: 'ozon-style',  label: 'Ozon' },
]

const MARKETPLACES = [
  { value: 'all',  label: 'Все' },
  { value: 'wb',   label: 'WB' },
  { value: 'ozon', label: 'Ozon' },
]

const GRADE_COLORS: Record<string, string> = {
  S: '#A78BFA', A: '#34D399', B: '#6E6AFC', C: '#FBBF24', D: '#EF4444',
}

const TABS = [
  { key: 'analyzer',   label: 'Анализ карточки', icon: Brain },
  { key: 'optimize',   label: 'AI Оптимизация',  icon: Zap },
  { key: 'compare',    label: 'WB vs Ozon',       icon: Scale },
  { key: 'benchmarks', label: 'Бенчмарки',        icon: BarChart2 },
]

const SEV_COLOR: Record<string, string> = { critical: '#EF4444', warning: '#FBBF24', tip: '#6E6AFC' }
const SEV_ICON:  Record<string, string> = { critical: '✕', warning: '⚠', tip: '→' }

// ── Score Ring ────────────────────────────────────────────────────────────────

function ScoreRing({ score, grade, size = 84 }: { score: number; grade: string; size?: number }) {
  const r    = (size - 8) / 2
  const circ = 2 * Math.PI * r
  const fill = (score / 100) * circ
  const color = GRADE_COLORS[grade] || '#6E6AFC'
  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={7} />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={7}
          strokeDasharray={`${fill} ${circ - fill}`} strokeLinecap="round"
          style={{ transition: 'stroke-dasharray 0.7s cubic-bezier(0.4,0,0.2,1)' }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontSize: 22, fontWeight: 800, color: '#FFF', lineHeight: 1 }}>{score}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color, letterSpacing: '0.06em', marginTop: 2 }}>{grade}</span>
      </div>
    </div>
  )
}

// ── Component Bar ─────────────────────────────────────────────────────────────

function ComponentBar({ label, score, maxScore, color }: { label: string; score: number; maxScore: number; color: string }) {
  const pct = Math.round((score / maxScore) * 100)
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
        <span style={{ fontSize: 11, color: '#888', fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 11, color: '#555' }}>{score}/{maxScore}</span>
      </div>
      <div style={{ height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 3 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.6s cubic-bezier(0.4,0,0.2,1)' }} />
      </div>
    </div>
  )
}

// ── Score Panel ───────────────────────────────────────────────────────────────

function ScorePanel({ score }: { score: CreativeScoreResponse }) {
  const color = GRADE_COLORS[score.grade] || '#6E6AFC'
  return (
    <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: 20 }}>
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', marginBottom: 20 }}>
        <ScoreRing score={score.total} grade={score.grade} />
        <div>
          <div style={{ fontSize: 13, color: '#666', marginBottom: 4 }}>Предсказанный CTR</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: score.predicted_ctr_uplift >= 0 ? '#34D399' : '#EF4444' }}>
            {score.predicted_ctr_uplift >= 0 ? '+' : ''}{score.predicted_ctr_uplift}%
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {score.strengths.map((s, i) => (
              <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10.5, color: '#34D399', background: 'rgba(52,211,153,0.08)', border: '1px solid rgba(52,211,153,0.18)', borderRadius: 20, padding: '3px 8px' }}>
                ✓ {s}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 20px' }}>
        {score.components.map(c => (
          <ComponentBar key={c.label} label={c.label} score={c.score} maxScore={c.max_score} color={color} />
        ))}
      </div>
      {score.issues.filter(i => i.severity === 'critical').length > 0 && (
        <div style={{ marginTop: 12, padding: '10px 12px', background: 'rgba(239,68,68,0.04)', border: '1px solid rgba(239,68,68,0.12)', borderRadius: 8 }}>
          <p style={{ fontSize: 10, fontWeight: 700, color: '#EF4444', letterSpacing: '0.08em', margin: '0 0 6px', textTransform: 'uppercase' }}>Критические проблемы</p>
          {score.issues.filter(i => i.severity === 'critical').map((issue, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 4 }}>
              <span style={{ color: '#EF4444', fontSize: 11, flexShrink: 0 }}>✕</span>
              <span style={{ fontSize: 11, color: '#904040', lineHeight: 1.4 }}>{issue.description}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Variant Card ──────────────────────────────────────────────────────────────

function VariantCard({ v }: { v: CreativeVariantItem }) {
  const color = GRADE_COLORS[v.score.grade] || '#6E6AFC'
  return (
    <div style={{ background: '#111113', border: v.rank === 1 ? `1px solid rgba(167,139,250,0.35)` : '1px solid rgba(255,255,255,0.07)', borderRadius: 12, padding: 16, position: 'relative' }}>
      {v.rank === 1 && (
        <div style={{ position: 'absolute', top: -10, right: 12, fontSize: 10, fontWeight: 700, color: '#A78BFA', background: '#111113', border: '1px solid rgba(167,139,250,0.35)', borderRadius: 20, padding: '2px 9px', letterSpacing: '0.06em' }}>
          BEST
        </div>
      )}
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
        <ScoreRing score={v.score.total} grade={v.score.grade} size={60} />
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#E0E0E0', marginBottom: 3 }}>{v.variant_name}</div>
          <div style={{ fontSize: 11, color: '#555', marginBottom: 2 }}>Пресет: {v.preset}</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: v.score.predicted_ctr_uplift >= 0 ? '#34D399' : '#EF4444' }}>
            CTR {v.score.predicted_ctr_uplift >= 0 ? '+' : ''}{v.score.predicted_ctr_uplift}%
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {v.score.components.map(c => (
          <ComponentBar key={c.label} label={c.label} score={c.score} maxScore={c.max_score} color={color} />
        ))}
      </div>
      {v.score.strengths.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {v.score.strengths.map((s, i) => (
            <div key={i} style={{ display: 'flex', gap: 5, marginBottom: 2 }}>
              <span style={{ color: '#34D399', fontSize: 10 }}>✓</span>
              <span style={{ fontSize: 10, color: '#5B8A6E', lineHeight: 1.4 }}>{s}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Issues List ──────────────────────────────────────────────────────────────

function IssueList({ issues }: { issues: CreativeIssue[] }) {
  if (issues.length === 0) return null
  const critical = issues.filter(i => i.severity === 'critical')
  const warnings = issues.filter(i => i.severity === 'warning')
  const tips     = issues.filter(i => i.severity === 'tip')
  const sorted   = [...critical, ...warnings, ...tips]
  const fixable  = sorted.reduce((s, i) => s + Math.abs(i.score_impact), 0)

  return (
    <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: 18, marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: '#555', letterSpacing: '0.10em', textTransform: 'uppercase' }}>
          Что тормозит карточку
        </span>
        {fixable > 0 && (
          <span style={{ fontSize: 10, fontWeight: 700, color: '#FBBF24', background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.20)', borderRadius: 20, padding: '2px 8px' }}>
            Потенциал: +{fixable} баллов
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {sorted.map((issue, i) => {
          const color = SEV_COLOR[issue.severity] || '#555'
          return (
            <div key={i} style={{ borderLeft: `3px solid ${color}30`, paddingLeft: 12 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color, background: `${color}15`, borderRadius: 3, padding: '1px 5px', textTransform: 'uppercase' as const }}>
                      {issue.severity}
                    </span>
                    <span style={{ fontSize: 11, color: '#C0C0C0', fontWeight: 600, lineHeight: 1.3 }}>{issue.description}</span>
                  </div>
                  <span style={{ fontSize: 11, color: '#555', lineHeight: 1.4 }}>{issue.fix_hint}</span>
                </div>
                {issue.score_impact !== 0 && (
                  <span style={{ fontSize: 11, fontWeight: 800, color, flexShrink: 0, whiteSpace: 'nowrap' }}>
                    {issue.score_impact} б.
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Marketplace Compare Panel ─────────────────────────────────────────────────

function MarketplaceComparePanel({ cmp }: { cmp: CreativeMarketplaceCompare }) {
  const GRADE_COLORS: Record<string, string> = {
    S: '#A78BFA', A: '#34D399', B: '#6E6AFC', C: '#FBBF24', D: '#EF4444',
  }

  function Side({ label, score, highlight }: { label: string; score: CreativeScoreResponse; highlight: boolean }) {
    const color = GRADE_COLORS[score.grade] || '#6E6AFC'
    return (
      <div style={{
        flex: 1, background: '#111113',
        border: highlight ? `1px solid ${color}40` : '1px solid rgba(255,255,255,0.07)',
        borderRadius: 12, padding: 16, position: 'relative',
      }}>
        {highlight && (
          <div style={{ position: 'absolute', top: -10, left: 12, fontSize: 9, fontWeight: 700, color, background: '#111113', border: `1px solid ${color}40`, borderRadius: 20, padding: '2px 8px', letterSpacing: '0.06em' }}>
            ЛУЧШЕ
          </div>
        )}
        <div style={{ textAlign: 'center', marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#888', marginBottom: 8 }}>{label}</div>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ fontSize: 36, fontWeight: 900, color: '#FFF', lineHeight: 1 }}>{score.total}</div>
            <div style={{ fontSize: 16, fontWeight: 800, color, marginTop: 2 }}>{score.grade}</div>
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: score.predicted_ctr_uplift >= 0 ? '#34D399' : '#EF4444', marginTop: 6 }}>
            CTR {score.predicted_ctr_uplift >= 0 ? '+' : ''}{score.predicted_ctr_uplift}%
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {score.components.map(c => {
            const pct = Math.round((c.score / c.max_score) * 100)
            return (
              <div key={c.label}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                  <span style={{ fontSize: 10, color: '#666' }}>{c.label}</span>
                  <span style={{ fontSize: 10, color: '#444', fontWeight: 700 }}>{c.score}/{c.max_score}</span>
                </div>
                <div style={{ height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }}>
                  <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 2, transition: 'width 0.5s' }} />
                </div>
              </div>
            )
          })}
        </div>
        {score.issues.length > 0 && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
            <p style={{ fontSize: 9, fontWeight: 700, color: '#444', letterSpacing: '0.08em', textTransform: 'uppercase', margin: '0 0 6px' }}>Проблемы</p>
            {score.issues.filter(i => i.severity !== 'tip').slice(0, 3).map((issue, i) => (
              <div key={i} style={{ display: 'flex', gap: 5, marginBottom: 4 }}>
                <span style={{ fontSize: 9.5, color: SEV_COLOR[issue.severity], flexShrink: 0 }}>
                  {SEV_ICON[issue.severity]}
                </span>
                <span style={{ fontSize: 10, color: '#666', lineHeight: 1.3 }}>{issue.description}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      {/* Delta header */}
      <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, padding: '12px 16px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Scale size={13} color="#A78BFA" />
        <span style={{ fontSize: 12, color: '#888' }}>Та же карточка на двух платформах</span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {cmp.better_for === 'equal' ? (
            <span style={{ fontSize: 11, color: '#555' }}>Результаты равнозначны</span>
          ) : (
            <>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#C0C0C0' }}>
                {cmp.better_for.toUpperCase()} лучше
              </span>
              <span style={{ fontSize: 13, fontWeight: 800, color: Math.abs(cmp.delta) > 10 ? '#EF4444' : '#FBBF24' }}>
                {cmp.delta > 0 ? '+' : ''}{cmp.delta} б.
              </span>
            </>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12 }}>
        <Side label="Wildberries" score={cmp.wb}   highlight={cmp.better_for === 'wb'} />
        <Side label="Ozon"        score={cmp.ozon} highlight={cmp.better_for === 'ozon'} />
      </div>

      {Math.abs(cmp.delta) >= 8 && (
        <div style={{ marginTop: 14, padding: '11px 14px', background: 'rgba(251,191,36,0.05)', border: '1px solid rgba(251,191,36,0.15)', borderRadius: 10 }}>
          <p style={{ fontSize: 11, color: '#907040', margin: 0, lineHeight: 1.5 }}>
            {cmp.better_for === 'wb'
              ? `Эта карточка значительно сильнее на WB (+${Math.abs(cmp.delta)} б.). На Ozon рекомендуем пресет «ozon-style» или «beauty» для выравнивания.`
              : `Эта карточка значительно сильнее на Ozon (+${Math.abs(cmp.delta)} б.). Для WB рекомендуем пресет «wb-style» или «tech».`}
          </p>
        </div>
      )}
    </div>
  )
}

// ── Benchmarks Panel ──────────────────────────────────────────────────────────

function BenchmarksPanel({ benchmarks }: { benchmarks: CreativeBenchmarksResponse }) {
  if (!benchmarks.has_data) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 20px', background: '#111113', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14 }}>
        <BarChart2 size={28} color="rgba(255,255,255,0.10)" style={{ marginBottom: 10 }} />
        <p style={{ fontSize: 13, color: '#444', margin: 0 }}>Бенчмарки появятся после 3+ генераций с результатами CTR.</p>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {benchmarks.preset_stats.length > 0 && (
        <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: 18 }}>
          <h3 style={{ fontSize: 11, fontWeight: 700, color: '#555', letterSpacing: '0.10em', textTransform: 'uppercase', margin: '0 0 14px' }}>По пресетам</h3>
          {benchmarks.preset_stats.map((row, i) => (
            <div key={row.preset} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <span style={{ fontSize: 11, color: '#888', width: 16, textAlign: 'right', flexShrink: 0 }}>{i + 1}</span>
              <span style={{ fontSize: 12, fontWeight: 600, color: '#C0C0C0', width: 80, flexShrink: 0 }}>{row.preset}</span>
              <div style={{ flex: 1, height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 3 }}>
                <div style={{
                  height: '100%', borderRadius: 3, background: '#6E6AFC',
                  width: `${Math.max(5, Math.min(100, (row.avg_ctr_uplift / 20) * 100))}%`,
                  transition: 'width 0.5s',
                }} />
              </div>
              <span style={{ fontSize: 12, fontWeight: 700, color: row.avg_ctr_uplift >= 0 ? '#34D399' : '#EF4444', width: 48, textAlign: 'right', flexShrink: 0 }}>
                {row.avg_ctr_uplift >= 0 ? '+' : ''}{row.avg_ctr_uplift}%
              </span>
              <span style={{ fontSize: 10, color: '#444', width: 28, textAlign: 'right', flexShrink: 0 }}>{row.count}x</span>
            </div>
          ))}
        </div>
      )}

      {benchmarks.category_stats.length > 0 && (
        <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: 18 }}>
          <h3 style={{ fontSize: 11, fontWeight: 700, color: '#555', letterSpacing: '0.10em', textTransform: 'uppercase', margin: '0 0 14px' }}>По категориям</h3>
          {benchmarks.category_stats.map((row, i) => (
            <div key={row.category} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <span style={{ fontSize: 11, color: '#888', width: 16, textAlign: 'right', flexShrink: 0 }}>{i + 1}</span>
              <span style={{ fontSize: 12, fontWeight: 600, color: '#C0C0C0', width: 100, flexShrink: 0 }}>{row.category}</span>
              <div style={{ flex: 1, height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 3 }}>
                <div style={{
                  height: '100%', borderRadius: 3, background: '#A78BFA',
                  width: `${Math.max(5, Math.min(100, (row.avg_ctr_uplift / 20) * 100))}%`,
                  transition: 'width 0.5s',
                }} />
              </div>
              <span style={{ fontSize: 12, fontWeight: 700, color: row.avg_ctr_uplift >= 0 ? '#34D399' : '#EF4444', width: 48, textAlign: 'right', flexShrink: 0 }}>
                {row.avg_ctr_uplift >= 0 ? '+' : ''}{row.avg_ctr_uplift}%
              </span>
              <span style={{ fontSize: 10, color: '#444', width: 28, textAlign: 'right', flexShrink: 0 }}>{row.count}x</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {benchmarks.top_preset && (
          <div style={{ background: 'rgba(110,106,252,0.06)', border: '1px solid rgba(110,106,252,0.18)', borderRadius: 10, padding: 14 }}>
            <div style={{ fontSize: 10, color: '#6E6AFC', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 4 }}>ЛУЧШИЙ ПРЕСЕТ</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: '#A78BFA' }}>{benchmarks.top_preset}</div>
          </div>
        )}
        {benchmarks.top_category && (
          <div style={{ background: 'rgba(52,211,153,0.06)', border: '1px solid rgba(52,211,153,0.18)', borderRadius: 10, padding: 14 }}>
            <div style={{ fontSize: 10, color: '#34D399', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 4 }}>ЛУЧШАЯ КАТЕГОРИЯ</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: '#34D399' }}>{benchmarks.top_category}</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SeoLabPage() {
  const [activeTab, setActiveTab] = useState('analyzer')

  // Analyzer form
  const [productName, setProductName] = useState('')
  const [category,    setCategory]    = useState('auto')
  const [preset,      setPreset]      = useState('premium')
  const [marketplace, setMarketplace] = useState('all')
  const [advantages,  setAdvantages]  = useState(['', '', ''])
  const [hasPhoto,    setHasPhoto]    = useState(false)

  // Instant scores via useMemo — no network, no debounce
  const score = useMemo<CreativeScoreResponse | null>(() => {
    if (!productName.trim()) return null
    return scoreCreative({
      product_name: productName.trim(), category, preset, marketplace,
      advantages: advantages.filter(Boolean), has_product_photo: hasPhoto,
    })
  }, [productName, category, preset, marketplace, advantages, hasPhoto])

  const compare = useMemo<CreativeMarketplaceCompare | null>(() => {
    if (!productName.trim()) return null
    const scores = scoreForBothMarketplaces({
      product_name: productName.trim(), category, preset,
      advantages: advantages.filter(Boolean), has_product_photo: hasPhoto,
    })
    const delta = scores.ozon.total - scores.wb.total
    return {
      wb: scores.wb, ozon: scores.ozon, delta,
      better_for: delta > 2 ? 'ozon' : delta < -2 ? 'wb' : 'equal',
    }
  }, [productName, category, preset, advantages, hasPhoto])
  const cmpLoad = false  // compare is computed synchronously (useMemo); never in a loading state

  // Optimize
  const [variants,    setVariants]    = useState<CreativeVariantItem[]>([])
  const [optLoading,  setOptLoading]  = useState(false)

  // Benchmarks
  const [benchmarks, setBenchmarks] = useState<CreativeBenchmarksResponse | null>(null)
  const [benchLoad,  setBenchLoad]  = useState(false)

  async function runOptimize() {
    if (!productName.trim()) return
    setOptLoading(true)
    setVariants([])
    try {
      const res = await api.creative.optimize({
        product_name: productName.trim(), category, marketplace,
        advantages: advantages.filter(Boolean), has_product_photo: hasPhoto,
      })
      setVariants(res.variants)
    } catch { /* silent */ } finally { setOptLoading(false) }
  }

  useEffect(() => {
    if (activeTab === 'benchmarks' && !benchmarks) {
      setBenchLoad(true)
      api.creative.benchmarks().then(setBenchmarks).catch(() => {}).finally(() => setBenchLoad(false))
    }
  }, [activeTab, benchmarks])


  const S = {
    page:  { minHeight: '100vh', background: '#0A0A0A', fontFamily: 'Inter, Arial, sans-serif' } as React.CSSProperties,
    inner: { maxWidth: 1100, margin: '0 auto', padding: '32px 24px 80px' } as React.CSSProperties,
    input: { width: '100%', background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 8, padding: '9px 11px', fontSize: 13, color: '#FFFFFF', outline: 'none', fontFamily: 'Inter, Arial, sans-serif', boxSizing: 'border-box' as const } as React.CSSProperties,
    select: { width: '100%', background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 8, padding: '9px 11px', fontSize: 13, color: '#FFFFFF', outline: 'none', fontFamily: 'Inter, Arial, sans-serif', boxSizing: 'border-box' as const, cursor: 'pointer', appearance: 'none' as const } as React.CSSProperties,
    lbl: { fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', color: '#666', textTransform: 'uppercase' as const, display: 'block', marginBottom: 5 },
  }

  return (
    <div style={S.page}>
      <div style={S.inner}>

        {/* Header */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Brain size={14} color="#6E6AFC" />
            </div>
            <h1 style={{ fontSize: 18, fontWeight: 700, color: '#FFF', margin: 0 }}>SEO Lab</h1>
          </div>
          <p style={{ fontSize: 12, color: '#444', margin: 0 }}>Анализ creative score · AI оптимизация вариантов · бенчмарки</p>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginBottom: 20, borderBottom: '1px solid #232329', paddingBottom: 0 }}>
          {TABS.map(t => {
            const Icon = t.icon
            const active = activeTab === t.key
            return (
              <button key={t.key} onClick={() => setActiveTab(t.key)} style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '9px 14px', background: 'none', border: 'none',
                borderBottom: active ? '2px solid #6E6AFC' : '2px solid transparent',
                cursor: 'pointer', color: active ? '#A78BFA' : '#555',
                fontSize: 12, fontWeight: active ? 700 : 500,
                marginBottom: -1, transition: 'color 0.15s',
              }}>
                <Icon size={12} />
                {t.label}
              </button>
            )
          })}
        </div>

        {/* Form + Result layout */}
        <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>

          {/* Left: form (shared across tabs) */}
          <div style={{ width: 290, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 12, position: 'sticky', top: 24 }}>
            <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, padding: '14px' }}>
              <p style={S.lbl}>Параметры карточки</p>

              <div style={{ marginBottom: 9 }}>
                <span style={S.lbl}>Название товара</span>
                <input value={productName} onChange={e => setProductName(e.target.value)} placeholder="Магнитные биты 6-13 мм" style={S.input} />
              </div>

              <div style={{ marginBottom: 9 }}>
                <span style={S.lbl}>Категория</span>
                <select value={category} onChange={e => setCategory(e.target.value)} style={S.select}>
                  {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </div>

              <div style={{ marginBottom: 9 }}>
                <span style={S.lbl}>Пресет</span>
                <select value={preset} onChange={e => setPreset(e.target.value)} style={S.select}>
                  {PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>

              <div style={{ marginBottom: 9 }}>
                <span style={S.lbl}>Маркетплейс</span>
                <select value={marketplace} onChange={e => setMarketplace(e.target.value)} style={S.select}>
                  {MARKETPLACES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>

              <div style={{ marginBottom: 9 }}>
                <span style={S.lbl}>Преимущества</span>
                {advantages.map((a, i) => (
                  <input key={i} value={a}
                    onChange={e => setAdvantages(prev => prev.map((v, j) => j === i ? e.target.value : v))}
                    placeholder={`Преимущество ${i + 1}`}
                    style={{ ...S.input, marginBottom: 5 }} />
                ))}
              </div>

              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={hasPhoto} onChange={e => setHasPhoto(e.target.checked)}
                  style={{ width: 14, height: 14, accentColor: '#6E6AFC' }} />
                <span style={{ fontSize: 12, color: '#888' }}>Есть фото товара</span>
              </label>
            </div>

            {activeTab === 'optimize' && (
              <button onClick={runOptimize} disabled={optLoading || !productName.trim()}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, background: '#6E6AFC', border: 'none', borderRadius: 10, padding: '11px', cursor: optLoading || !productName.trim() ? 'not-allowed' : 'pointer', color: '#FFF', fontSize: 13, fontWeight: 700, opacity: optLoading || !productName.trim() ? 0.6 : 1 }}>
                {optLoading ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Zap size={13} />}
                {optLoading ? 'Анализирую…' : 'Подобрать 3 варианта'}
              </button>
            )}
          </div>

          {/* Right: results */}
          <div style={{ flex: 1, minWidth: 0 }}>

            {/* Analyzer tab — instant, no loading state */}
            {activeTab === 'analyzer' && (
              <div>
                {score ? (
                  <>
                    <ScorePanel score={score} />
                    <IssueList issues={score.issues} />
                    {score.improvement_potential > score.total && (
                      <div style={{ marginTop: 12, padding: '10px 14px', background: 'rgba(110,106,252,0.05)', border: '1px solid rgba(110,106,252,0.15)', borderRadius: 10 }}>
                        <span style={{ fontSize: 11, color: '#7B78C0' }}>
                          Максимально достижимый балл: <strong style={{ color: '#A78BFA' }}>{score.improvement_potential}</strong> после исправления всех проблем
                        </span>
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ textAlign: 'center', padding: '48px 20px', background: '#111113', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14 }}>
                    <Brain size={32} color="rgba(255,255,255,0.08)" style={{ marginBottom: 12 }} />
                    <p style={{ fontSize: 13, color: '#444', margin: 0 }}>Введите название товара слева — оценка появится мгновенно</p>
                  </div>
                )}
              </div>
            )}

            {/* Optimize tab */}
            {activeTab === 'optimize' && (
              <div>
                {optLoading && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '14px 16px', background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, marginBottom: 12 }}>
                    <Loader2 size={13} color="#6E6AFC" style={{ animation: 'spin 1s linear infinite' }} />
                    <span style={{ fontSize: 12, color: '#888' }}>Генерирую 3 варианта…</span>
                  </div>
                )}
                {variants.length > 0 && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
                    {variants.map(v => <VariantCard key={v.variant_name} v={v} />)}
                  </div>
                )}
                {variants.length === 0 && !optLoading && (
                  <div style={{ textAlign: 'center', padding: '48px 20px', background: '#111113', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14 }}>
                    <Zap size={32} color="rgba(255,255,255,0.08)" style={{ marginBottom: 12 }} />
                    <p style={{ fontSize: 13, color: '#444', margin: '0 0 8px' }}>
                      AI сравнит 3 варианта пресетов и покажет предсказанный CTR для каждого
                    </p>
                    <p style={{ fontSize: 11, color: '#333', margin: 0 }}>Bigger Product · Minimal Text · High Contrast</p>
                  </div>
                )}
              </div>
            )}

            {/* Compare tab */}
            {activeTab === 'compare' && (
              <div>
                {cmpLoad && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '14px 16px', background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12 }}>
                    <Loader2 size={13} color="#555" style={{ animation: 'spin 1s linear infinite' }} />
                    <span style={{ fontSize: 12, color: '#555' }}>Сравниваю платформы…</span>
                  </div>
                )}
                {compare && !cmpLoad && <MarketplaceComparePanel cmp={compare} />}
                {!compare && !cmpLoad && (
                  <div style={{ textAlign: 'center', padding: '48px 20px', background: '#111113', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14 }}>
                    <Scale size={32} color="rgba(255,255,255,0.08)" style={{ marginBottom: 12 }} />
                    <p style={{ fontSize: 13, color: '#444', margin: 0 }}>Введите параметры карточки и нажмите «Сравнить WB vs Ozon»</p>
                  </div>
                )}
              </div>
            )}

            {/* Benchmarks tab */}
            {activeTab === 'benchmarks' && (
              <div>
                {benchLoad && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '14px 16px', background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12 }}>
                    <Loader2 size={13} color="#555" style={{ animation: 'spin 1s linear infinite' }} />
                    <span style={{ fontSize: 12, color: '#555' }}>Загрузка бенчмарков…</span>
                  </div>
                )}
                {benchmarks && !benchLoad && <BenchmarksPanel benchmarks={benchmarks} />}
              </div>
            )}
          </div>
        </div>
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        select option { background: #111113; color: #fff; }
      `}</style>
    </div>
  )
}

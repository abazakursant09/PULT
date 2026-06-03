'use client'

import React, {
  useState, useEffect, useRef, createRef, useMemo,
} from 'react'
import { useSearchParams } from 'next/navigation'
import {
  Upload, Sparkles, Download, Loader2, AlertCircle,
  CheckCircle2, X, Image as ImageIcon, RefreshCw,
  ChevronDown, ChevronUp, Save, Wand2, Eye, EyeOff,
  Archive, Trash2, Brain, TrendingUp, ChevronRight,
} from 'lucide-react'
import {
  api,
  type SeoProjectItem,
  type ImportFinanceSummary,
  type CreativeScoreResponse,
  type CreativeIssue,
  type CreativeVariantItem,
} from '@/lib/api'
import { scoreCreative, appendScoreHistory, getScoreTrend } from '@/lib/creative-scorer'
import { trackEvent } from '@/lib/events'
import { SeoRecommendationPanel } from '@/components/SeoRecommendationPanel'
import {
  CARD_COMPONENTS, CARD_LABELS, CARD_ORDER,
  TYPOGRAPHY_PRESETS, DEFAULT_TYPOGRAPHY_PRESET,
  CARD_W, CARD_H,
  type CardData,
} from './cards'

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

const STAGES = [
  { key: 'queued',     label: 'Очередь' },
  { key: 'generating', label: 'Генерация' },
  { key: 'retrying',   label: 'Повтор' },
  { key: 'done',       label: 'Готово' },
]

const PLACEHOLDER_BG = 'linear-gradient(135deg, #1e1e22 0%, #2a2a30 100%)'
const PREVIEW_SCALE  = 0.5
const PREVIEW_W      = Math.round(CARD_W * PREVIEW_SCALE)  // 270
const PREVIEW_H      = Math.round(CARD_H * PREVIEW_SCALE)  // 360

// ── CRC32 + ZIP builder ───────────────────────────────────────────────────────

const _crc32Table = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1)
    t[n] = c
  }
  return t
})()

function crc32(data: Uint8Array): number {
  let c = 0xFFFFFFFF
  for (let i = 0; i < data.length; i++) c = _crc32Table[(c ^ data[i]) & 0xFF] ^ (c >>> 8)
  return (c ^ 0xFFFFFFFF) >>> 0
}

function u32le(a: number[], v: number) { a.push(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF) }
function u16le(a: number[], v: number) { a.push(v & 0xFF, (v >> 8) & 0xFF) }

async function buildZip(files: { name: string; data: Uint8Array }[]): Promise<Uint8Array> {
  const local: number[]   = []
  const central: number[] = []
  const offsets: number[] = []

  for (const f of files) {
    offsets.push(local.length)
    const name = new TextEncoder().encode(f.name)
    const crc  = crc32(f.data)
    const sz   = f.data.length
    u32le(local, 0x04034b50); u16le(local, 20); u16le(local, 0); u16le(local, 0)
    u16le(local, 0); u16le(local, 0); u32le(local, crc)
    u32le(local, sz); u32le(local, sz)
    u16le(local, name.length); u16le(local, 0)
    name.forEach(b => local.push(b))
    f.data.forEach(b => local.push(b))
  }

  const cdOff = local.length
  for (let i = 0; i < files.length; i++) {
    const f    = files[i]
    const name = new TextEncoder().encode(f.name)
    const crc  = crc32(f.data)
    const sz   = f.data.length
    u32le(central, 0x02014b50); u16le(central, 20); u16le(central, 20)
    u16le(central, 0); u16le(central, 0); u16le(central, 0); u16le(central, 0)
    u32le(central, crc); u32le(central, sz); u32le(central, sz)
    u16le(central, name.length); u16le(central, 0); u16le(central, 0)
    u16le(central, 0); u16le(central, 0); u32le(central, 0); u32le(central, offsets[i])
    name.forEach(b => central.push(b))
  }

  const eocd: number[] = []
  u32le(eocd, 0x06054b50); u16le(eocd, 0); u16le(eocd, 0)
  u16le(eocd, files.length); u16le(eocd, files.length)
  u32le(eocd, central.length); u32le(eocd, cdOff); u16le(eocd, 0)

  return new Uint8Array([...local, ...central, ...eocd])
}

// ── Render card → PNG bytes ───────────────────────────────────────────────────

async function renderCardToPng(el: HTMLElement): Promise<Uint8Array | null> {
  // Source card size — matches CARD_W × CARD_H from cards.tsx
  const SW = 540, SH = 720
  // Export at 2× for 1080×1440 retina output
  const EW = SW * 2, EH = SH * 2
  const ns = 'http://www.w3.org/2000/svg'

  const fo = document.createElementNS(ns, 'foreignObject')
  fo.setAttribute('x', '0'); fo.setAttribute('y', '0')
  fo.setAttribute('width', String(SW)); fo.setAttribute('height', String(SH))
  const wrapper = document.createElement('div')
  wrapper.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml')
  wrapper.style.cssText = `width:${SW}px;height:${SH}px;overflow:hidden;`
  wrapper.innerHTML = el.outerHTML
  fo.appendChild(wrapper)
  const svg = document.createElementNS(ns, 'svg')
  svg.setAttribute('xmlns', ns)
  svg.setAttribute('width', String(SW)); svg.setAttribute('height', String(SH))
  svg.appendChild(fo)
  const svgBlob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml;charset=utf-8' })
  const svgUrl  = URL.createObjectURL(svgBlob)
  return new Promise<Uint8Array | null>(resolve => {
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = EW; canvas.height = EH
      const ctx = canvas.getContext('2d')!
      ctx.imageSmoothingEnabled = true
      ctx.imageSmoothingQuality = 'high'
      ctx.scale(2, 2); ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(svgUrl)
      canvas.toBlob(blob => {
        if (!blob) { resolve(null); return }
        blob.arrayBuffer().then(buf => resolve(new Uint8Array(buf)))
      }, 'image/png')
    }
    img.onerror = () => { URL.revokeObjectURL(svgUrl); resolve(null) }
    img.src = svgUrl
  })
}

async function exportCardToPng(el: HTMLElement, filename: string): Promise<void> {
  const bytes = await renderCardToPng(el)
  if (!bytes) throw new Error('Export failed')
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([bytes as BlobPart], { type: 'image/png' }))
  link.download = filename
  document.body.appendChild(link); link.click(); document.body.removeChild(link)
}

// ── Styles ────────────────────────────────────────────────────────────────────

const S = {
  page:    { minHeight: '100vh', background: '#0A0A0A', fontFamily: 'Inter, Arial, sans-serif' } as React.CSSProperties,
  inner:   { maxWidth: 1400, margin: '0 auto', padding: '32px 24px 80px', display: 'flex', gap: 28, alignItems: 'flex-start' } as React.CSSProperties,
  form:    { width: 334, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10, position: 'sticky', top: 24 } as React.CSSProperties,
  cards:   { flex: 1, minWidth: 0 } as React.CSSProperties,
  panel:   { background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12 } as React.CSSProperties,
  section: { padding: '12px 14px' } as React.CSSProperties,
  lbl:     { fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', color: '#666', textTransform: 'uppercase' as const, display: 'block', marginBottom: 6 },
  input:   { width: '100%', background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 8, padding: '9px 11px', fontSize: 13, color: '#FFFFFF', outline: 'none', fontFamily: 'Inter, Arial, sans-serif', boxSizing: 'border-box' as const } as React.CSSProperties,
  select:  { width: '100%', background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 8, padding: '9px 11px', fontSize: 13, color: '#FFFFFF', outline: 'none', fontFamily: 'Inter, Arial, sans-serif', boxSizing: 'border-box' as const, cursor: 'pointer', appearance: 'none' as const } as React.CSSProperties,
}

function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={S.panel}>
      <div style={S.section}>
        <span style={{ ...S.lbl, marginBottom: 10 }}>{title}</span>
        {children}
      </div>
    </div>
  )
}

// ── Stage progress ────────────────────────────────────────────────────────────

function StageProgress({ stage, progress, completedCount, slideStatuses }: {
  stage: string
  progress: number
  completedCount: number
  slideStatuses: string[]
}) {
  const stageIdx = STAGES.findIndex(s => s.key === stage)
  return (
    <div style={{ ...S.panel, padding: '12px 16px', marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        {STAGES.map((s, i) => {
          const past    = i < stageIdx
          const current = i === stageIdx
          return (
            <React.Fragment key={s.key}>
              {i > 0 && <div style={{ flex: 1, height: 1, background: past || current ? '#6E6AFC' : 'rgba(255,255,255,0.08)' }} />}
              <div style={{ display: 'flex', alignItems: 'center', gap: 4, opacity: i > stageIdx ? 0.3 : 1 }}>
                <div style={{
                  width: 7, height: 7, borderRadius: '50%',
                  background: current ? '#6E6AFC' : (past ? '#10B981' : 'rgba(255,255,255,0.18)'),
                  boxShadow: current ? '0 0 5px #6E6AFC' : 'none',
                }} />
                <span style={{ fontSize: 10, fontWeight: 600, color: current ? '#A78BFA' : (past ? '#10B981' : '#555') }}>
                  {s.label}
                </span>
              </div>
            </React.Fragment>
          )
        })}
      </div>
      <div style={{ height: 3, background: 'rgba(255,255,255,0.07)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${progress}%`, background: 'linear-gradient(90deg, #6E6AFC, #A78BFA)', borderRadius: 2, transition: 'width 0.4s' }} />
      </div>
      {slideStatuses.length > 0 && (
        <div style={{ display: 'flex', gap: 5, marginTop: 7, alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: '#555' }}>Слайды:</span>
          {slideStatuses.map((st, i) => (
            <div key={i} title={`Слайд ${i + 1}: ${st}`} style={{
              width: 9, height: 9, borderRadius: '50%', transition: 'background 0.3s',
              background: st === 'done' ? '#10B981' : st === 'failed' ? '#EF4444' : 'rgba(110,106,252,0.4)',
            }} />
          ))}
          <span style={{ fontSize: 10, color: '#555', marginLeft: 2 }}>{completedCount}/6</span>
        </div>
      )}
    </div>
  )
}

// ── Safe zone overlay ─────────────────────────────────────────────────────────

function SafeZoneOverlay({ marketplace }: { marketplace: string }) {
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 20 }}>
      {/* Title safe zone */}
      <div style={{ position: 'absolute', left: '6%', right: '6%', top: '4%', height: '26%', border: '1px dashed rgba(150,140,255,0.22)', borderRadius: 3 }}>
        <span style={{ position: 'absolute', top: 2, left: 3, fontSize: 7, color: 'rgba(150,140,255,0.45)', lineHeight: 1 }}>title</span>
      </div>
      {/* CTA safe zone */}
      <div style={{ position: 'absolute', left: '6%', right: '6%', bottom: '5%', height: '20%', border: '1px dashed rgba(80,200,120,0.22)', borderRadius: 3 }}>
        <span style={{ position: 'absolute', bottom: 2, left: 3, fontSize: 7, color: 'rgba(80,200,120,0.45)', lineHeight: 1 }}>cta</span>
      </div>
      {/* WB mobile crop */}
      {marketplace !== 'ozon' && (
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '8%', borderBottom: '1px dashed rgba(220,80,80,0.18)' }}>
          <span style={{ position: 'absolute', bottom: 2, left: 3, fontSize: 7, color: 'rgba(220,80,80,0.38)', lineHeight: 1 }}>wb crop</span>
        </div>
      )}
    </div>
  )
}

// ── Creative Score ring ───────────────────────────────────────────────────────

const GRADE_COLORS: Record<string, string> = {
  S: '#A78BFA', A: '#34D399', B: '#6E6AFC', C: '#FBBF24', D: '#EF4444',
}

function ScoreRing({ score, grade, size = 72 }: { score: number; grade: string; size?: number }) {
  const r    = (size - 8) / 2
  const circ = 2 * Math.PI * r
  const fill = (score / 100) * circ
  const color = GRADE_COLORS[grade] || '#6E6AFC'
  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={6} />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={6}
          strokeDasharray={`${fill} ${circ - fill}`} strokeLinecap="round"
          style={{ transition: 'stroke-dasharray 0.6s cubic-bezier(0.4,0,0.2,1)' }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontSize: size > 60 ? 18 : 13, fontWeight: 800, color: '#FFF', lineHeight: 1 }}>{score}</span>
        <span style={{ fontSize: 9, fontWeight: 700, color, letterSpacing: '0.08em', marginTop: 1 }}>{grade}</span>
      </div>
    </div>
  )
}

const SEV_COLOR: Record<string, string> = { critical: '#EF4444', warning: '#FBBF24', tip: '#6E6AFC' }
const SEV_ICON:  Record<string, string> = { critical: '✕', warning: '⚠', tip: '→' }

function IssueRow({ issue, onAutoFix }: { issue: CreativeIssue; onAutoFix: (fix: { action: string; value: string }) => void }) {
  const color = SEV_COLOR[issue.severity] || '#555'
  return (
    <div style={{ borderLeft: `2px solid ${color}20`, paddingLeft: 8, marginBottom: 7 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 5, justifyContent: 'space-between' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
            <span style={{ fontSize: 9, color, flexShrink: 0 }}>{SEV_ICON[issue.severity]}</span>
            <span style={{ fontSize: 10, color: '#999', fontWeight: 600, lineHeight: 1.3 }}>{issue.description}</span>
          </div>
          <span style={{ fontSize: 9.5, color: '#555', lineHeight: 1.3 }}>{issue.fix_hint}</span>
        </div>
        {issue.score_impact !== 0 && (
          <span style={{ fontSize: 9, color, flexShrink: 0, marginLeft: 4, fontWeight: 700, whiteSpace: 'nowrap' }}>
            {issue.score_impact} б.
          </span>
        )}
      </div>
      {issue.auto_fix && (
        <button onClick={() => onAutoFix(issue.auto_fix!)}
          style={{ marginTop: 4, fontSize: 9.5, color: '#6E6AFC', background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.18)', borderRadius: 4, padding: '3px 7px', cursor: 'pointer', fontWeight: 700 }}>
          {issue.auto_fix.label}
        </button>
      )}
    </div>
  )
}

function CreativeScoreWidget({
  score, prevScore, onAutoFix, trend,
}: {
  score: CreativeScoreResponse
  prevScore: number | null
  onAutoFix: (fix: { action: string; value: string }) => void
  trend?: { delta: number; sessions: number } | null
}) {
  const color = GRADE_COLORS[score.grade] || '#6E6AFC'
  const delta = prevScore !== null ? score.total - prevScore : null
  const criticalCount  = score.issues.filter(i => i.severity === 'critical').length
  const potentialGain  = score.improvement_potential - score.total

  return (
    <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, padding: '12px 14px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <Brain size={11} color="#A78BFA" />
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', color: '#666', textTransform: 'uppercase', flex: 1 }}>Creative Score</span>
        {trend && trend.delta !== 0 && (
          <span style={{ fontSize: 9, color: trend.delta > 0 ? '#34D399' : '#EF4444', flexShrink: 0 }}>
            {trend.delta > 0 ? '↑' : '↓'} {trend.delta > 0 ? '+' : ''}{trend.delta} б. / {trend.sessions} сес.
          </span>
        )}
        {delta !== null && delta !== 0 && (
          <span style={{
            fontSize: 10, fontWeight: 800,
            color: delta > 0 ? '#34D399' : '#EF4444',
            background: delta > 0 ? 'rgba(52,211,153,0.10)' : 'rgba(239,68,68,0.10)',
            border: `1px solid ${delta > 0 ? 'rgba(52,211,153,0.25)' : 'rgba(239,68,68,0.25)'}`,
            borderRadius: 20, padding: '2px 7px',
          }}>
            {delta > 0 ? '+' : ''}{delta}
          </span>
        )}
      </div>

      {/* Ring + CTR */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 10 }}>
        <ScoreRing score={score.total} grade={score.grade} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, color: '#888', marginBottom: 2 }}>Предсказанный CTR</div>
          <div style={{ fontSize: 15, fontWeight: 800, color: score.predicted_ctr_uplift >= 0 ? '#34D399' : '#EF4444' }}>
            {score.predicted_ctr_uplift >= 0 ? '+' : ''}{score.predicted_ctr_uplift}%
          </div>
          {potentialGain > 0 && (
            <div style={{ marginTop: 5, fontSize: 9.5, color: '#FBBF24' }}>
              Потенциал: до {score.improvement_potential} (+{potentialGain} б.)
            </div>
          )}
        </div>
      </div>

      {/* Component bars */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 8 }}>
        {score.components.map(c => {
          const pct = Math.round((c.score / c.max_score) * 100)
          return (
            <div key={c.label}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                <span style={{ fontSize: 9.5, color: '#555', fontWeight: 600 }}>{c.label}</span>
                <span style={{ fontSize: 9.5, color: '#444' }}>{c.score}/{c.max_score}</span>
              </div>
              <div style={{ height: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 2 }}>
                <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 2, transition: 'width 0.5s' }} />
              </div>
            </div>
          )
        })}
      </div>

      {/* Strengths */}
      {score.strengths.map((s, i) => (
        <div key={i} style={{ display: 'flex', gap: 5, marginBottom: 3 }}>
          <span style={{ color: '#34D399', fontSize: 10, flexShrink: 0 }}>✓</span>
          <span style={{ fontSize: 10, color: '#5B8A6E', lineHeight: 1.4 }}>{s}</span>
        </div>
      ))}

      {/* Issues */}
      {score.issues.length > 0 && (
        <div style={{ marginTop: 9, paddingTop: 9, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 7 }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: '#555', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              Проблемы ({score.issues.length})
            </span>
            {criticalCount > 0 && (
              <span style={{ fontSize: 9, fontWeight: 700, color: '#EF4444', background: 'rgba(239,68,68,0.10)', borderRadius: 3, padding: '1px 5px' }}>
                {criticalCount} критичных
              </span>
            )}
          </div>
          {score.issues.filter(i => i.severity !== 'tip').map((issue, i) => (
            <IssueRow key={i} issue={issue} onAutoFix={onAutoFix} />
          ))}
          {score.issues.filter(i => i.severity === 'tip').map((issue, i) => (
            <IssueRow key={`tip_${i}`} issue={issue} onAutoFix={onAutoFix} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── AI Optimization panel ─────────────────────────────────────────────────────

function AiVariantCard({
  v, selected, onClick,
}: {
  v: CreativeVariantItem
  selected: boolean
  onClick: () => void
}) {
  const color = GRADE_COLORS[v.score.grade] || '#6E6AFC'
  return (
    <button onClick={onClick} style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
      borderRadius: 9, cursor: 'pointer', width: '100%', textAlign: 'left',
      background: selected ? 'rgba(110,106,252,0.10)' : 'transparent',
      border: selected ? '1px solid rgba(110,106,252,0.35)' : '1px solid rgba(255,255,255,0.07)',
      transition: 'all 0.15s',
    }}>
      <ScoreRing score={v.score.total} grade={v.score.grade} size={44} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: selected ? '#A78BFA' : '#C0C0C0' }}>
          {v.rank === 1 && <span style={{ marginRight: 4 }}>🏆</span>}{v.variant_name}
        </div>
        <div style={{ fontSize: 10, color: '#555', marginTop: 2 }}>
          {v.score.predicted_ctr_uplift >= 0 ? '+' : ''}{v.score.predicted_ctr_uplift}% CTR · {v.preset}
        </div>
      </div>
      <ChevronRight size={11} color={selected ? '#A78BFA' : '#444'} />
    </button>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SeoCardsPage() {
  const searchParams = useSearchParams()

  // Form
  const [photo, setPhoto]               = useState<string | null>(null)
  const [category, setCategory]         = useState('auto')
  const [preset, setPreset]             = useState('premium')
  const [typographyPreset, setTypoPreset] = useState(DEFAULT_TYPOGRAPHY_PRESET)
  const [marketplace, setMarketplace]   = useState('all')
  const [productName, setPName]         = useState('')
  const [currentPrice, setCPrice]       = useState('')
  const [oldPrice, setOPrice]           = useState('')
  const [advantages, setAdvs]           = useState(['', '', ''])
  const [showSafeZones, setShowSafeZones] = useState(false)

  // Generation
  const [status, setStatus]             = useState<'idle' | 'generating' | 'done' | 'error'>('idle')
  const [taskId, setTaskId]             = useState<string | null>(null)
  const [backgrounds, setBgs]           = useState<string[]>([])
  const [slideStatuses, setSlideStatuses] = useState<string[]>([])
  const [stage, setStage]               = useState('queued')
  const [progress, setProgress]         = useState(0)
  const [completedCount, setCompleted]  = useState(0)
  const [error, setError]               = useState('')
  const [exporting, setExporting]       = useState<number | null>(null)
  const [exportingZip, setExportingZip] = useState(false)
  const [retrying, setRetrying]         = useState<number | null>(null)

  // AI + projects
  const [suggestLoading, setSuggestLoading] = useState(false)
  const [projects, setProjects]         = useState<SeoProjectItem[]>([])
  const [projectsOpen, setProjectsOpen] = useState(false)
  const [savingProject, setSavingProject] = useState(false)
  const [importBanner, setImportBanner] = useState<ImportFinanceSummary | null>(null)

  // Creative Score — computed instantly client-side, no network round-trip
  const [prevScore, setPrevScore] = useState<number | null>(null)

  // AI Optimization
  const [aiOptMode, setAiOptMode]             = useState(false)
  const [aiVariants, setAiVariants]           = useState<CreativeVariantItem[]>([])
  const [aiOptLoading, setAiOptLoading]       = useState(false)
  const [selectedVariant, setSelectedVariant] = useState<CreativeVariantItem | null>(null)

  const exportRefs = useRef(CARD_ORDER.map(() => createRef<HTMLDivElement>()))

  useEffect(() => {
    api.csvImport.financeSummary().then(setImportBanner).catch(() => {})
    api.seoCards.listProjects().then(setProjects).catch(() => {})
  }, [])

  // Auto-prefill + auto-trigger from Action Engine "Авто-пересборка"
  const autoTriggered = useRef(false)
  useEffect(() => {
    const qProduct  = searchParams.get('product')
    const qCategory = searchParams.get('category')
    const qAuto     = searchParams.get('auto') === '1'
    if (qProduct) setPName(qProduct)
    if (qCategory) setCategory(qCategory)
    if (qAuto && qProduct && !autoTriggered.current) {
      autoTriggered.current = true
      // Short delay to let state settle before triggering
      const t = setTimeout(() => {
        setError('')
        setStatus('generating')
        setStage('queued')
        setProgress(0)
        setCompleted(0)
        setSlideStatuses(Array(6).fill('pending'))
        setBgs([])
        api.seoCards.generate({ preset: 'premium', category: qCategory || 'auto', product_name: qProduct.trim() })
          .then(res => {
            setTaskId(res.task_id)
            trackEvent('seo_rebuild_started', 'seo_cards', qProduct.trim(), { reason: 'auto_from_insight', preset: 'premium' })
            api.rebuildTracker.track({
              product_name:   qProduct.trim(),
              category:       qCategory || 'auto',
              preset:         'premium',
              rebuild_reason: 'auto_from_insight',
            }).catch(() => {})
          })
          .catch(e => { setError(e instanceof Error ? e.message : 'Ошибка'); setStatus('error') })
      }, 300)
      return () => clearTimeout(t)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Card data ───────────────────────────────────────────────────────────────

  const typography = TYPOGRAPHY_PRESETS[typographyPreset]

  const cardData: Omit<CardData, 'background'> = {
    productName,
    currentPrice,
    oldPrice,
    advantages,
    brandName:    productName.split(' ').slice(0, 2).join(' '),
    productPhoto: photo,
    typography,
    marketplace,
  }

  function bgFor(idx: number): string {
    return backgrounds[idx] || PLACEHOLDER_BG
  }

  // ── Photo upload ────────────────────────────────────────────────────────────

  function handlePhotoFile(file: File) {
    if (!file.type.startsWith('image/')) return
    const r = new FileReader()
    r.onload = e => setPhoto(e.target?.result as string)
    r.readAsDataURL(file)
  }

  // ── Generation ──────────────────────────────────────────────────────────────

  async function handleGenerate() {
    if (!productName.trim()) { setError('Укажите название товара'); return }
    setError('')
    setStatus('generating')
    setStage('queued')
    setProgress(0)
    setCompleted(0)
    setSlideStatuses(Array(6).fill('pending'))
    setBgs([])
    try {
      const res = await api.seoCards.generate({ preset, category, product_name: productName.trim() })
      setTaskId(res.task_id)
      trackEvent('seo_rebuild_started', 'seo_cards', productName.trim(), { reason: 'manual_user_request', preset })
      // Track rebuild asynchronously — don't block generation
      api.rebuildTracker.track({
        product_name:      productName.trim(),
        marketplace,
        category,
        preset,
        typography_preset: typographyPreset,
        rebuild_reason:    'manual_user_request',
      }).catch(() => {})
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка создания задачи')
      setStatus('error')
    }
  }

  // ── Polling ─────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (status !== 'generating' || !taskId) return
    const iv = setInterval(async () => {
      try {
        const res = await api.seoCards.taskStatus(taskId)
        if (res.stage)             setStage(res.stage)
        if (res.progress != null)  setProgress(res.progress)
        if (res.completed_count != null) setCompleted(res.completed_count)
        if (res.slide_statuses)    setSlideStatuses(res.slide_statuses)
        if (res.status === 'done' && res.image_urls) {
          setBgs(res.image_urls)
          setSlideStatuses(Array(6).fill('done'))
          setProgress(100); setCompleted(6); setStage('done')
          setStatus('done')
          trackEvent('seo_rebuild_completed', 'seo_cards', productName.trim(), { slides: res.image_urls.length })
          clearInterval(iv)
        } else if (res.status === 'error') {
          setError('Генерация завершилась с ошибкой')
          setStatus('error')
          clearInterval(iv)
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Ошибка опроса')
        setStatus('error')
        clearInterval(iv)
      }
    }, 2500)
    return () => clearInterval(iv)
  }, [status, taskId])

  // ── Per-slide retry ─────────────────────────────────────────────────────────

  async function handleRetrySingle(idx: number) {
    if (!productName.trim()) return
    setRetrying(idx)
    try {
      const res = await api.seoCards.retrySingle({ preset, category, product_name: productName.trim(), slide_idx: idx })
      setBgs(prev => { const next = [...prev]; next[idx] = res.background_url; return next })
      setSlideStatuses(prev => { const next = [...prev]; next[idx] = 'done'; return next })
    } catch {
      // old background stays
    } finally {
      setRetrying(null)
    }
  }

  // ── AI suggest ──────────────────────────────────────────────────────────────

  async function handleSuggest() {
    if (!productName.trim()) { setError('Сначала введите название товара'); return }
    setError('')
    setSuggestLoading(true)
    try {
      const res = await api.seoCards.suggestText(productName.trim(), category)
      setAdvs(res.benefit_suggestions.slice(0, 3))
    } catch {
      // silently ignore
    } finally {
      setSuggestLoading(false)
    }
  }

  // ── PNG export ──────────────────────────────────────────────────────────────

  async function handleDownload(idx: number) {
    const ref = exportRefs.current[idx]
    if (!ref?.current) return
    setExporting(idx)
    try {
      await exportCardToPng(ref.current, `seo-${CARD_ORDER[idx]}-${Date.now()}.png`)
    } catch {
      alert('Не удалось экспортировать PNG.')
    } finally {
      setExporting(null)
    }
  }

  // ── ZIP export ──────────────────────────────────────────────────────────────

  async function handleDownloadZip() {
    setExportingZip(true)
    try {
      const files: { name: string; data: Uint8Array }[] = []
      for (let i = 0; i < CARD_ORDER.length; i++) {
        const ref = exportRefs.current[i]
        if (!ref?.current) continue
        const bytes = await renderCardToPng(ref.current)
        if (bytes) files.push({ name: `slide-${i + 1}-${CARD_ORDER[i]}.png`, data: bytes })
      }
      if (files.length === 0) { alert('Нет слайдов для экспорта'); return }
      const zip  = await buildZip(files)
      const slug = (productName.trim().replace(/\s+/g, '-') || 'seo-cards').slice(0, 40)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(new Blob([zip as BlobPart], { type: 'application/zip' }))
      link.download = `${slug}-${Date.now()}.zip`
      document.body.appendChild(link); link.click(); document.body.removeChild(link)
    } catch {
      alert('Ошибка экспорта ZIP')
    } finally {
      setExportingZip(false)
    }
  }

  // ── Save / load project ─────────────────────────────────────────────────────

  async function handleSaveProject() {
    if (!productName.trim()) { setError('Укажите название товара'); return }
    setSavingProject(true)
    try {
      const saved = await api.seoCards.saveProject({
        name:              productName.trim(),
        product_name:      productName.trim(),
        marketplace,
        preset,
        category,
        typography_preset: typographyPreset,
        current_price:     currentPrice,
        old_price:         oldPrice,
        advantages:        advantages.filter(Boolean),
        image_urls:        backgrounds.filter(Boolean),
      })
      setProjects(prev => [saved, ...prev.slice(0, 19)])
      setProjectsOpen(true)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка сохранения')
    } finally {
      setSavingProject(false)
    }
  }

  function handleLoadProject(p: SeoProjectItem) {
    setPName(p.product_name)
    setPreset(p.preset)
    setCategory(p.category)
    setTypoPreset(p.typography_preset || DEFAULT_TYPOGRAPHY_PRESET)
    setMarketplace(p.marketplace)
    setCPrice(p.current_price || '')
    setOPrice(p.old_price || '')
    const advs = p.advantages
    setAdvs(advs.length >= 3 ? advs.slice(0, 3) : [...advs, ...Array(3 - advs.length).fill('')])
    if (p.image_urls.length > 0) {
      setBgs(p.image_urls)
      setStatus('done')
      setStage('done')
      setProgress(100)
      setCompleted(6)
    }
  }

  async function handleDeleteProject(id: string, e: React.MouseEvent) {
    e.stopPropagation()
    try {
      await api.seoCards.deleteProject(id)
      setProjects(prev => prev.filter(p => p.id !== id))
    } catch {}
  }

  function handleReset() {
    setStatus('idle'); setTaskId(null); setBgs([]); setError('')
    setStage('queued'); setProgress(0); setCompleted(0); setSlideStatuses([])
  }

  // Instant client-side creative score via useMemo — no debounce, no spinner
  const creativeScore = useMemo<CreativeScoreResponse | null>(() => {
    if (!productName.trim()) return null
    return scoreCreative({
      product_name: productName.trim(),
      category, preset, marketplace,
      advantages: advantages.filter(Boolean),
      has_product_photo: !!photo,
    })
  }, [productName, category, preset, marketplace, advantages, photo])

  // Load previous score from history when product name changes
  useEffect(() => {
    if (!productName.trim()) { setPrevScore(null); return }
    const key = productName.trim().toLowerCase()
    const hist = (() => { try { const s = localStorage.getItem(`chist_${key}`); return s ? JSON.parse(s) : [] } catch { return [] } })()
    setPrevScore(hist.length > 0 ? hist[hist.length - 1].score : null)
  }, [productName])

  // Persist score to history on change (deduped by scorer)
  useEffect(() => {
    if (!creativeScore || !productName.trim()) return
    appendScoreHistory(productName.trim().toLowerCase(), {
      score: creativeScore.total, grade: creativeScore.grade, preset,
    })
  }, [creativeScore?.total, productName, preset]) // eslint-disable-line

  function applyAutoFix(fix: { action: string; value: string }) {
    if (fix.action === 'set_preset')      setPreset(fix.value)
    if (fix.action === 'set_marketplace') setMarketplace(fix.value)
  }

  async function handleAiOptimize() {
    if (!productName.trim()) { setError('Укажите название товара'); return }
    setAiOptLoading(true)
    setAiVariants([])
    setSelectedVariant(null)
    try {
      const res = await api.creative.optimize({
        product_name: productName.trim(),
        category, marketplace,
        advantages: advantages.filter(Boolean),
        has_product_photo: !!photo,
      })
      setAiVariants(res.variants)
      setSelectedVariant(res.variants[0] || null)
    } catch { /* silent */ } finally {
      setAiOptLoading(false)
    }
  }

  function applyVariant(v: CreativeVariantItem) {
    setPreset(v.preset)
    setSelectedVariant(v)
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={S.page}>
      <div style={S.inner}>

        {/* ── LEFT: Form ── */}
        <aside style={S.form}>

          {/* Header */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 3 }}>
              <div style={{ width: 28, height: 28, borderRadius: 7, background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.25)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Sparkles size={13} color="#6E6AFC" />
              </div>
              <h1 style={{ fontSize: 16, fontWeight: 700, color: '#FFFFFF', margin: 0 }}>SEO-карточки 2.0</h1>
            </div>
            <p style={{ fontSize: 11, color: '#444', margin: 0 }}>AI генерирует фоны · текст накладывается фронтом</p>
          </div>

          {/* AI Recommendation Panel */}
          <SeoRecommendationPanel
            onApplyStyle={(styleName) => {
              // Map style name back to preset
              const lower = styleName.toLowerCase()
              if (lower.includes('bigger product'))  setPreset('premium')
              else if (lower.includes('minimal'))    setPreset('minimal')
              else if (lower.includes('warm'))       setPreset('beauty')
              else if (lower.includes('high contrast')) setPreset('tech')
            }}
          />

          {/* Import banner */}
          {importBanner?.has_data && (
            <div style={{ background: 'rgba(16,185,129,0.05)', border: '1px solid rgba(16,185,129,0.18)', borderRadius: 10, padding: '10px 12px' }}>
              <div style={{ fontSize: 11, color: '#10B981', fontWeight: 700, marginBottom: 4 }}>Данные из импорта</div>
              <p style={{ fontSize: 11, color: '#666', margin: '0 0 7px', lineHeight: 1.4 }}>
                {importBanner.by_product?.[0]?.title
                  ? `Найден товар: ${importBanner.by_product[0].title}`
                  : `${importBanner.row_count} строк фин. данных`}
              </p>
              {importBanner.by_product?.[0] && (
                <button
                  onClick={() => {
                    const p = importBanner.by_product[0]
                    setPName(p.title)
                    if (!currentPrice && p.sales > 0)
                      setCPrice(String(Math.round(p.revenue / p.sales)))
                  }}
                  style={{ fontSize: 11, color: '#10B981', background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: 6, padding: '4px 9px', cursor: 'pointer', fontWeight: 600 }}
                >
                  Заполнить
                </button>
              )}
            </div>
          )}

          {/* Photo upload */}
          <FormSection title="Фото товара">
            <label
              onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handlePhotoFile(f) }}
              onDragOver={e => e.preventDefault()}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                gap: 7, height: 100, borderRadius: 9, cursor: 'pointer',
                border: '2px dashed rgba(110,106,252,0.30)',
                background: photo ? 'transparent' : 'rgba(110,106,252,0.03)',
                backgroundImage: photo ? `url("${photo}")` : undefined,
                backgroundSize: 'cover', backgroundPosition: 'center',
                position: 'relative', overflow: 'hidden',
              }}
            >
              <input type="file" accept="image/*" onChange={e => { const f = e.target.files?.[0]; if (f) handlePhotoFile(f) }} style={{ display: 'none' }} />
              {!photo && <>
                <Upload size={18} color="#6E6AFC" />
                <span style={{ fontSize: 11, color: '#555', textAlign: 'center', lineHeight: 1.4 }}>Перетащите или кликните</span>
              </>}
              {photo && (
                <button type="button" onClick={e => { e.preventDefault(); setPhoto(null) }}
                  style={{ position: 'absolute', top: 5, right: 5, width: 18, height: 18, borderRadius: '50%', background: 'rgba(0,0,0,0.65)', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <X size={10} color="#fff" />
                </button>
              )}
            </label>
          </FormSection>

          {/* Typography presets */}
          <FormSection title="Типографика">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {Object.entries(TYPOGRAPHY_PRESETS).map(([key, cfg]) => (
                <button key={key} onClick={() => setTypoPreset(key)}
                  style={{
                    padding: '3px 9px', borderRadius: 20, fontSize: 10, fontWeight: 700, cursor: 'pointer',
                    border: typographyPreset === key ? `1px solid ${cfg.accentColor}` : '1px solid rgba(255,255,255,0.09)',
                    background: typographyPreset === key ? cfg.accentColorLight : 'transparent',
                    color: typographyPreset === key ? cfg.accentColor : '#666',
                    transition: 'all 0.15s',
                  }}>
                  {cfg.label}
                </button>
              ))}
            </div>
          </FormSection>

          {/* Style */}
          <FormSection title="Стиль AI-фона">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div>
                <span style={S.lbl}>Категория</span>
                <select value={category} onChange={e => setCategory(e.target.value)} style={S.select}>
                  {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </div>
              <div>
                <span style={S.lbl}>Пресет</span>
                <select value={preset} onChange={e => setPreset(e.target.value)} style={S.select}>
                  {PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>
              <div>
                <span style={S.lbl}>Маркетплейс (CTA)</span>
                <div style={{ display: 'flex', gap: 5 }}>
                  {MARKETPLACES.map(m => (
                    <button key={m.value} onClick={() => setMarketplace(m.value)}
                      style={{ flex: 1, padding: '6px', borderRadius: 7, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                        border: marketplace === m.value ? '1px solid #6E6AFC' : '1px solid rgba(255,255,255,0.09)',
                        background: marketplace === m.value ? 'rgba(110,106,252,0.12)' : 'transparent',
                        color: marketplace === m.value ? '#A78BFA' : '#555' }}>
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </FormSection>

          {/* Product info */}
          <FormSection title="Товар">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              <div>
                <span style={S.lbl}>Название *</span>
                <input value={productName} onChange={e => setPName(e.target.value)} placeholder="Магнитные биты 6-13 мм" style={S.input} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 7 }}>
                <div>
                  <span style={S.lbl}>Цена ₽</span>
                  <input value={currentPrice} onChange={e => setCPrice(e.target.value)} placeholder="490" style={S.input} />
                </div>
                <div>
                  <span style={S.lbl}>Старая ₽</span>
                  <input value={oldPrice} onChange={e => setOPrice(e.target.value)} placeholder="790" style={S.input} />
                </div>
              </div>
            </div>
          </FormSection>

          {/* Advantages + AI */}
          <FormSection title="Преимущества">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {advantages.map((adv, i) => (
                <input key={i} value={adv}
                  onChange={e => setAdvs(prev => prev.map((v, j) => j === i ? e.target.value : v))}
                  placeholder={`Преимущество ${i + 1}`} style={S.input} />
              ))}
              <button onClick={handleSuggest} disabled={suggestLoading}
                style={{ marginTop: 3, display: 'flex', alignItems: 'center', gap: 5, background: 'rgba(110,106,252,0.07)', border: '1px solid rgba(110,106,252,0.18)', borderRadius: 7, padding: '6px 11px', cursor: suggestLoading ? 'not-allowed' : 'pointer', color: '#A78BFA', fontSize: 11, fontWeight: 700, opacity: suggestLoading ? 0.6 : 1 }}>
                {suggestLoading ? <Loader2 size={11} style={{ animation: 'spin 1s linear infinite' }} /> : <Wand2 size={11} />}
                AI предложит тексты
              </button>
            </div>
          </FormSection>

          {/* Creative Score widget — instant, no loading state */}
          {creativeScore && (
            <CreativeScoreWidget
              score={creativeScore}
              prevScore={prevScore}
              onAutoFix={applyAutoFix}
              trend={getScoreTrend(productName.trim().toLowerCase(), creativeScore.total)}
            />
          )}

          {/* AI Optimization mode */}
          <div style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, padding: '12px 14px' }}>
            <button onClick={() => { setAiOptMode(v => !v); if (!aiOptMode && aiVariants.length === 0) handleAiOptimize() }}
              style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginBottom: aiOptMode ? 8 : 0 }}>
              <Brain size={11} color={aiOptMode ? '#A78BFA' : '#555'} />
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', color: aiOptMode ? '#A78BFA' : '#555', textTransform: 'uppercase', flex: 1, textAlign: 'left' }}>
                AI Оптимизация
              </span>
              {aiOptLoading
                ? <Loader2 size={10} color="#555" style={{ animation: 'spin 1s linear infinite' }} />
                : <TrendingUp size={10} color={aiOptMode ? '#A78BFA' : '#444'} />}
            </button>
            {aiOptMode && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {aiVariants.length === 0 && !aiOptLoading && (
                  <button onClick={handleAiOptimize}
                    style={{ fontSize: 11, color: '#A78BFA', background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.18)', borderRadius: 7, padding: '6px 10px', cursor: 'pointer', fontWeight: 700 }}>
                    Подобрать 3 варианта
                  </button>
                )}
                {aiVariants.map(v => (
                  <AiVariantCard key={v.variant_name} v={v}
                    selected={selectedVariant?.variant_name === v.variant_name}
                    onClick={() => applyVariant(v)} />
                ))}
                {aiVariants.length > 0 && (
                  <p style={{ fontSize: 10, color: '#444', margin: '4px 0 0', lineHeight: 1.4 }}>
                    Нажмите вариант — пресет применится. Затем нажмите «Сгенерировать».
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Safe zones toggle */}
          <button onClick={() => setShowSafeZones(v => !v)}
            style={{ display: 'flex', alignItems: 'center', gap: 7, background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, padding: '7px 11px', cursor: 'pointer', color: showSafeZones ? '#A78BFA' : '#555', fontSize: 11, fontWeight: 600 }}>
            {showSafeZones ? <Eye size={12} /> : <EyeOff size={12} />}
            Безопасные зоны
          </button>

          {/* Error */}
          {error && (
            <div style={{ background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 9, padding: '9px 12px', display: 'flex', alignItems: 'flex-start', gap: 7 }}>
              <AlertCircle size={13} color="#EF4444" style={{ flexShrink: 0, marginTop: 1 }} />
              <span style={{ fontSize: 12, color: '#EF4444', lineHeight: 1.5 }}>{error}</span>
            </div>
          )}

          {/* Generate button */}
          <button
            onClick={status === 'done' ? handleReset : handleGenerate}
            disabled={status === 'generating'}
            style={{
              width: '100%', background: status === 'done' ? '#111113' : '#6E6AFC',
              border: status === 'done' ? '1px solid rgba(255,255,255,0.10)' : 'none',
              color: '#FFFFFF', fontWeight: 700, fontSize: 14, borderRadius: 10,
              padding: '12px', cursor: status === 'generating' ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
              opacity: status === 'generating' ? 0.65 : 1, transition: 'opacity 0.2s',
            }}
          >
            {status === 'generating' && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
            {status === 'done'
              ? <><RefreshCw size={13} /> Сгенерировать заново</>
              : status === 'generating'
                ? 'Генерация…'
                : <><Sparkles size={13} /> Сгенерировать серию</>}
          </button>

          {/* Save project */}
          {(status === 'done' || backgrounds.length > 0) && (
            <button onClick={handleSaveProject} disabled={savingProject}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, background: 'rgba(16,185,129,0.07)', border: '1px solid rgba(16,185,129,0.18)', borderRadius: 10, padding: '10px', cursor: savingProject ? 'not-allowed' : 'pointer', color: '#10B981', fontSize: 12, fontWeight: 700, opacity: savingProject ? 0.65 : 1 }}>
              {savingProject ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={13} />}
              Сохранить проект
            </button>
          )}
        </aside>

        {/* ── RIGHT: Cards ── */}
        <main style={S.cards}>

          {/* Stage progress */}
          {status === 'generating' && (
            <StageProgress stage={stage} progress={progress} completedCount={completedCount} slideStatuses={slideStatuses} />
          )}

          {/* Done bar */}
          {status === 'done' && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 14, background: 'rgba(16,185,129,0.07)', border: '1px solid rgba(16,185,129,0.18)', borderRadius: 10, padding: '9px 14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <CheckCircle2 size={14} color="#10B981" />
                <span style={{ fontSize: 12, color: '#10B981', fontWeight: 700 }}>6 фонов готовы</span>
              </div>
              <button onClick={handleDownloadZip} disabled={exportingZip}
                style={{ display: 'flex', alignItems: 'center', gap: 5, background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.20)', borderRadius: 8, padding: '6px 12px', cursor: exportingZip ? 'not-allowed' : 'pointer', color: '#A78BFA', fontSize: 12, fontWeight: 700, opacity: exportingZip ? 0.6 : 1 }}>
                {exportingZip ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : <Archive size={12} />}
                Скачать ZIP
              </button>
            </div>
          )}

          {/* Cards grid 3×2 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
            {CARD_ORDER.map((type, idx) => {
              const CardComp = CARD_COMPONENTS[type]
              const bg       = bgFor(idx)
              const isExp    = exporting === idx
              const isRet    = retrying === idx
              const slideSt  = slideStatuses[idx] || 'pending'

              return (
                <div key={type} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {/* Preview */}
                  <div style={{ width: PREVIEW_W, height: PREVIEW_H, overflow: 'hidden', position: 'relative', borderRadius: 9, border: '1px solid rgba(255,255,255,0.07)', background: '#111113' }}>
                    {/* Generation overlay */}
                    {status === 'generating' && (
                      <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 9 }}>
                        {slideSt === 'done'
                          ? <CheckCircle2 size={18} color="#10B981" />
                          : slideSt === 'failed'
                            ? <AlertCircle size={18} color="#EF4444" />
                            : <Loader2 size={18} color="#6E6AFC" style={{ animation: 'spin 1s linear infinite' }} />}
                      </div>
                    )}
                    {/* Safe zones */}
                    {showSafeZones && status === 'done' && <SafeZoneOverlay marketplace={marketplace} />}
                    {/* Scaled card */}
                    <div style={{ transform: `scale(${PREVIEW_SCALE})`, transformOrigin: 'top left', width: CARD_W, height: CARD_H }}>
                      <CardComp data={{ ...cardData, background: bg }} />
                    </div>
                  </div>

                  {/* Label + buttons */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <span style={{ fontSize: 10, fontWeight: 600, color: '#555', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {idx + 1}. {CARD_LABELS[type]}
                    </span>
                    {status === 'done' && (
                      <button onClick={() => handleRetrySingle(idx)} disabled={isRet}
                        title="Перегенерировать слайд"
                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 6, cursor: isRet ? 'not-allowed' : 'pointer', opacity: isRet ? 0.5 : 1 }}>
                        {isRet ? <Loader2 size={9} color="#666" style={{ animation: 'spin 1s linear infinite' }} /> : <RefreshCw size={9} color="#666" />}
                      </button>
                    )}
                    <button onClick={() => handleDownload(idx)} disabled={isExp}
                      title="PNG 1200×1200"
                      style={{ display: 'flex', alignItems: 'center', gap: 3, background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.16)', borderRadius: 6, padding: '4px 8px', fontSize: 10, fontWeight: 700, color: '#A78BFA', cursor: isExp ? 'not-allowed' : 'pointer', opacity: isExp ? 0.6 : 1 }}>
                      {isExp ? <Loader2 size={9} style={{ animation: 'spin 1s linear infinite' }} /> : <Download size={9} />}
                      PNG
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Empty state */}
          {status === 'idle' && backgrounds.length === 0 && (
            <div style={{ marginTop: 16, textAlign: 'center', padding: '28px 20px', background: '#111113', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 12 }}>
              <ImageIcon size={28} color="rgba(255,255,255,0.10)" style={{ marginBottom: 10 }} />
              <p style={{ fontSize: 13, color: '#444', margin: 0, lineHeight: 1.7 }}>
                Заполните форму и нажмите «Сгенерировать серию».<br />
                AI создаст 6 фонов за ~10 секунд.
              </p>
            </div>
          )}

          {/* Saved projects */}
          {projects.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <button onClick={() => setProjectsOpen(v => !v)}
                style={{ display: 'flex', alignItems: 'center', gap: 7, background: 'none', border: 'none', cursor: 'pointer', color: '#555', fontSize: 12, fontWeight: 600, padding: '6px 0', width: '100%' }}>
                {projectsOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                Сохранённые проекты ({projects.length})
              </button>
              {projectsOpen && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 8 }}>
                  {projects.map(p => (
                    <div key={p.id} onClick={() => handleLoadProject(p)}
                      style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 9, padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10 }}>
                      {p.image_urls[0] && (
                        <div style={{ width: 34, height: 34, borderRadius: 6, overflow: 'hidden', flexShrink: 0 }}>
                          <img src={p.image_urls[0]} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                        </div>
                      )}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: '#D0D0D0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name || p.product_name}</div>
                        <div style={{ fontSize: 10, color: '#444', marginTop: 2 }}>{p.created_at} · {p.image_urls.length} фонов</div>
                      </div>
                      <button onClick={e => handleDeleteProject(p.id, e)}
                        style={{ flexShrink: 0, width: 24, height: 24, borderRadius: 6, background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.14)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <Trash2 size={10} color="#EF4444" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </main>
      </div>

      {/* Hidden full-size cards for PNG/ZIP export */}
      <div aria-hidden="true" style={{ position: 'fixed', left: -9999, top: 0, pointerEvents: 'none', zIndex: -1 }}>
        {CARD_ORDER.map((type, idx) => {
          const CardComp = CARD_COMPONENTS[type]
          return (
            <div key={type} ref={exportRefs.current[idx]}>
              <CardComp data={{ ...cardData, background: bgFor(idx) }} />
            </div>
          )
        })}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        select option { background: #111113; color: #fff; }
      `}</style>
    </div>
  )
}

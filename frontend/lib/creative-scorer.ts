// TypeScript port of backend/services/creative_scorer.py
// Pure heuristics — runs client-side in < 1ms, zero network.

import type { CreativeScoreResponse, CreativeIssue, CreativeAutoFix } from './api'

// ── Layout table ──────────────────────────────────────────────────────────────

interface Layout {
  top_heavy:     boolean
  bottom_cta:    boolean
  wb_contrast:   number
  ozon_contrast: number
  product_size:  'large' | 'medium' | 'full'
}

const _LAYOUT: Record<string, Layout> = {
  'premium':     { top_heavy: true,  bottom_cta: false, wb_contrast: 17, ozon_contrast: 21, product_size: 'large'  },
  'sale':        { top_heavy: true,  bottom_cta: true,  wb_contrast: 21, ozon_contrast: 18, product_size: 'medium' },
  'minimal':     { top_heavy: false, bottom_cta: false, wb_contrast: 13, ozon_contrast: 17, product_size: 'large'  },
  'tech':        { top_heavy: false, bottom_cta: false, wb_contrast: 24, ozon_contrast: 21, product_size: 'medium' },
  'beauty':      { top_heavy: true,  bottom_cta: false, wb_contrast: 16, ozon_contrast: 23, product_size: 'full'   },
  'marketplace': { top_heavy: false, bottom_cta: true,  wb_contrast: 22, ozon_contrast: 18, product_size: 'medium' },
  'luxury':      { top_heavy: false, bottom_cta: false, wb_contrast: 23, ozon_contrast: 20, product_size: 'large'  },
  'wb-style':    { top_heavy: false, bottom_cta: true,  wb_contrast: 25, ozon_contrast: 15, product_size: 'large'  },
  'ozon-style':  { top_heavy: false, bottom_cta: false, wb_contrast: 15, ozon_contrast: 25, product_size: 'large'  },
}

const _FIT: Record<string, Record<string, number>> = {
  auto:        { tech: 10, premium: 9, minimal: 7, sale: 6, luxury: 5, beauty: 3, marketplace: 7, 'wb-style': 7, 'ozon-style': 7 },
  beauty:      { beauty: 10, luxury: 9, premium: 7, minimal: 6, sale: 7, tech: 3, marketplace: 6, 'wb-style': 6, 'ozon-style': 7 },
  electronics: { tech: 10, premium: 8, minimal: 7, sale: 6, luxury: 5, beauty: 3, marketplace: 8, 'wb-style': 7, 'ozon-style': 8 },
  home:        { premium: 10, minimal: 9, luxury: 8, beauty: 6, sale: 7, tech: 5, marketplace: 7, 'wb-style': 7, 'ozon-style': 7 },
  clothes:     { luxury: 10, beauty: 9, premium: 8, minimal: 7, sale: 8, tech: 3, marketplace: 6, 'wb-style': 7, 'ozon-style': 7 },
  sport:       { tech: 10, premium: 8, minimal: 7, sale: 9, luxury: 5, beauty: 4, marketplace: 7, 'wb-style': 7, 'ozon-style': 7 },
}

const _BEST_PRESET: Record<string, string> = {
  auto: 'tech', beauty: 'beauty', electronics: 'tech',
  home: 'premium', clothes: 'luxury', sport: 'tech',
}

const _PRESET_LABELS: Record<string, string> = {
  premium: 'Premium', sale: 'Sale', minimal: 'Minimal', tech: 'Tech',
  beauty: 'Beauty', marketplace: 'Market', luxury: 'Luxury',
  'wb-style': 'WB', 'ozon-style': 'Ozon',
}
const _CAT_LABELS: Record<string, string> = {
  auto: 'Авто', beauty: 'Красота', home: 'Дом',
  electronics: 'Электроника', clothes: 'Одежда', sport: 'Спорт',
}

function _grade(total: number): string {
  if (total >= 88) return 'S'
  if (total >= 75) return 'A'
  if (total >= 60) return 'B'
  if (total >= 40) return 'C'
  return 'D'
}

// ── Params ────────────────────────────────────────────────────────────────────

export interface ScoreParams {
  product_name:      string
  category:          string
  preset:            string
  marketplace:       string   // "all" | "wb" | "ozon"
  advantages:        string[]
  has_product_photo: boolean
}

// ── Core scorer ───────────────────────────────────────────────────────────────

export function scoreCreative(p: ScoreParams): CreativeScoreResponse {
  const issues: CreativeIssue[] = []
  const strengths: string[] = []
  const layout = _LAYOUT[p.preset] ?? _LAYOUT['premium']

  // ── product_coverage (0-30) ──────────────────────────────────────────────
  let coverage: number
  if (p.has_product_photo) {
    coverage = layout.product_size === 'large' ? 28 : layout.product_size === 'full' ? 26 : 22
    strengths.push('Фото товара добавлено')
  } else {
    coverage = 5
    issues.push({
      issue_type: 'no_photo', severity: 'critical',
      description: 'Нет фото товара — самая частая причина низкого CTR',
      fix_hint: 'Добавьте фото в поле «Фото товара» слева',
      score_impact: coverage - 28,
      auto_fix: null,
    })
  }

  // ── text_density (0-20) ──────────────────────────────────────────────────
  const filled = p.advantages.filter(a => a && a.trim())
  const n = filled.length
  let density: number

  if (n === 0) {
    density = 3
    issues.push({
      issue_type: 'no_advantages', severity: 'critical',
      description: 'Нет текстовых блоков — карточка неинформативна',
      fix_hint: 'Добавьте 2-3 конкретных преимущества (до 30 символов)',
      score_impact: -17, auto_fix: null,
    })
  } else if (n === 1) {
    density = 11
    issues.push({
      issue_type: 'single_advantage', severity: 'warning',
      description: 'Только 1 преимущество — добавьте ещё 1-2 для полноты',
      fix_hint: 'Покупатели сканируют 2-3 буллета за 1.5 секунды',
      score_impact: -9, auto_fix: null,
    })
  } else {
    density = n === 2 ? 16 : 20
    strengths.push(`${n} текстовых блока — хороший охват внимания`)
  }

  for (let i = 0; i < filled.length; i++) {
    const adv = filled[i]
    if (adv.length > 32) {
      density = Math.max(density - 2, 0)
      const short = adv.slice(0, 28).trimEnd() + '…'
      issues.push({
        issue_type: `text_long_${i}`, severity: 'warning',
        description: `Текст #${i + 1}: ${adv.length} симв. — WB обрезает после 30 на мобильных`,
        fix_hint: `Пример: «${short}»`,
        score_impact: -2, auto_fix: null,
      })
    }
  }

  if (n >= 2 && filled.every(a => a.length <= 28)) {
    strengths.push('Тексты оптимальной длины для мобильных')
  }

  // ── visual_contrast (0-25) — differs by marketplace ──────────────────────
  let contrast: number
  if (p.marketplace === 'wb') {
    contrast = layout.wb_contrast
  } else if (p.marketplace === 'ozon') {
    contrast = layout.ozon_contrast
  } else {
    contrast = Math.round((layout.wb_contrast + layout.ozon_contrast) / 2)
  }

  if (contrast <= 15) {
    const best_for_mp = p.marketplace === 'ozon' ? 'ozon-style' : 'tech'
    issues.push({
      issue_type: 'low_contrast', severity: 'warning',
      description: `Пресет «${_PRESET_LABELS[p.preset] ?? p.preset}» слабо выделяется на фоне ${p.marketplace !== 'all' ? p.marketplace.toUpperCase() : 'каталога'}`,
      fix_hint: 'Попробуйте пресет с высоким контрастом',
      score_impact: contrast - 24,
      auto_fix: { action: 'set_preset', value: best_for_mp, label: `Применить ${_PRESET_LABELS[best_for_mp] ?? best_for_mp}` },
    })
  } else if (contrast >= 22) {
    strengths.push(`Высокий контраст на ${p.marketplace !== 'all' ? p.marketplace.toUpperCase() : 'маркетплейсах'}`)
  }

  // ── mobile_safety (0-15) — WB/Ozon rules ─────────────────────────────────
  let mobile = 15

  if (p.marketplace === 'ozon') {
    if (!layout.top_heavy) strengths.push('Ozon: полная видимость, безопасная зона')
  } else {
    if (layout.top_heavy) {
      mobile -= 4
      issues.push({
        issue_type: 'wb_top_crop',
        severity: p.marketplace === 'all' ? 'warning' : 'critical',
        description: 'Заголовок в зоне обрезки WB (верхние 8% = 58px из 720px)',
        fix_hint: 'Пресеты без обрезки: Tech, Luxury, Minimal, WB, Ozon',
        score_impact: -4,
        auto_fix: { action: 'set_preset', value: 'tech', label: 'Применить Tech (безопасный)' },
      })
    }
    if (layout.bottom_cta && (p.marketplace === 'wb' || p.marketplace === 'all')) {
      mobile -= 3
      issues.push({
        issue_type: 'wb_bottom_cover', severity: 'warning',
        description: 'CTA-блок в нижних 15% — перекрывается ценником WB в каталоге',
        fix_hint: 'Пресеты без нижнего CTA: Premium, Minimal, Tech, Luxury',
        score_impact: -3,
        auto_fix: { action: 'set_preset', value: 'luxury', label: 'Применить Luxury (чистый низ)' },
      })
    }
    if (mobile >= 14) strengths.push('Безопасное расположение элементов для WB')
  }

  // ── category_fit (0-10) ──────────────────────────────────────────────────
  const catMap     = _FIT[p.category] ?? {}
  const catFit     = catMap[p.preset]     ?? 6
  const bestPreset = _BEST_PRESET[p.category] ?? 'premium'
  const bestFit    = catMap[bestPreset]    ?? 9
  const catLabel    = _CAT_LABELS[p.category] ?? p.category
  const presetLabel = _PRESET_LABELS[p.preset] ?? p.preset

  if (catFit >= 9) {
    strengths.push(`Пресет «${presetLabel}» оптимален для «${catLabel}»`)
  } else if (catFit <= 4) {
    const bestLabel = _PRESET_LABELS[bestPreset] ?? bestPreset
    issues.push({
      issue_type: 'cat_mismatch', severity: 'warning',
      description: `«${presetLabel}» слабо подходит категории «${catLabel}» (fit ${catFit}/10 vs ${bestFit}/10)`,
      fix_hint: `Оптимальный пресет для «${catLabel}»: ${bestLabel}`,
      score_impact: catFit - bestFit,
      auto_fix: { action: 'set_preset', value: bestPreset, label: `Применить ${bestLabel}` },
    })
  }

  if (p.category === 'clothes' && (p.marketplace === 'ozon' || p.marketplace === 'all') && n > 0) {
    issues.push({
      issue_type: 'ozon_clothes_policy', severity: 'tip',
      description: 'Ozon рекомендует для одежды: минимум текста, акцент на фото модели',
      fix_hint: 'Пресет Minimal или Beauty лучше отображает одежду на Ozon',
      score_impact: 0,
      auto_fix: { action: 'set_marketplace', value: 'wb', label: 'Сделать под WB' },
    })
  }

  // ── Total ─────────────────────────────────────────────────────────────────
  const total = Math.min(100, coverage + density + contrast + mobile + catFit)
  const grade = _grade(total)
  const uplift = Math.round((total - 60) * 0.3 * 10) / 10

  if (grade === 'S' || grade === 'A') {
    strengths.push('Карточка готова к публикации')
  } else if (grade === 'D') {
    issues.push({
      issue_type: 'overall_critical', severity: 'critical',
      description: 'Карточка требует доработки — комплексные проблемы снижают CTR',
      fix_hint: 'Устраните проблемы выше, начните с критических',
      score_impact: 0, auto_fix: null,
    })
  }

  const fixablePts = issues.reduce((s, i) => i.score_impact < 0 ? s + Math.abs(i.score_impact) : s, 0)

  return {
    total,
    grade,
    predicted_ctr_uplift: uplift,
    improvement_potential: Math.min(100, total + fixablePts),
    best_preset_for_cat: bestPreset,
    components: [
      { label: 'Товар',     score: coverage, max_score: 30 },
      { label: 'Текст',     score: density,  max_score: 20 },
      { label: 'Контраст',  score: contrast, max_score: 25 },
      { label: 'Мобайл',    score: mobile,   max_score: 15 },
      { label: 'Категория', score: catFit,   max_score: 10 },
    ],
    strengths: strengths.slice(0, 4),
    issues,
  }
}

// ── WB vs Ozon dual score ─────────────────────────────────────────────────────

export function scoreForBothMarketplaces(
  p: Omit<ScoreParams, 'marketplace'>
): { wb: CreativeScoreResponse; ozon: CreativeScoreResponse } {
  return {
    wb:   scoreCreative({ ...p, marketplace: 'wb'   }),
    ozon: scoreCreative({ ...p, marketplace: 'ozon' }),
  }
}

// ── Score history — "the system learns" ───────────────────────────────────────

export interface ScoreSnapshot {
  score:  number
  grade:  string
  preset: string
  ts:     number
}

export function loadScoreHistory(productKey: string): ScoreSnapshot[] {
  try {
    const s = localStorage.getItem(`chist_${productKey}`)
    return s ? (JSON.parse(s) as ScoreSnapshot[]) : []
  } catch { return [] }
}

export function appendScoreHistory(productKey: string, snap: Omit<ScoreSnapshot, 'ts'>): void {
  try {
    const hist = loadScoreHistory(productKey)
    const now  = Date.now()
    const last = hist[hist.length - 1]
    if (last && last.score === snap.score && (now - last.ts) < 300_000) return
    const updated = [...hist, { ...snap, ts: now }].slice(-10)
    localStorage.setItem(`chist_${productKey}`, JSON.stringify(updated))
  } catch {}
}

export function getScoreTrend(productKey: string, currentScore: number): { delta: number; sessions: number } | null {
  const hist = loadScoreHistory(productKey)
  if (hist.length < 1) return null
  const delta = currentScore - hist[0].score
  return { delta, sessions: hist.length + 1 }
}

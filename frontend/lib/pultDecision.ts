// ── L1 ПУЛЬТ — causal decision trust system ──────────────────────────────────
// Strict layers: FACT (raw money), STATE (demo / timeframe), INTERPRETATION
// (problem / actions / buckets) + CAUSAL TRUST — evidence (source/volume/period),
// mechanism (causal chain), confidence (level/reason). Everything derived
// deterministically from the existing DTOs; nothing fabricated. Where the data
// is absent (period, period-delta), it is stated as unknown / null, never faked.
// Confidence reuses the REAL backend confidence_level — not invented.
// Frontend-only over /api/insights + /api/finance/summary.

import type { InsightItem, FinanceSummaryItem } from '@/lib/api'

export type L1Mode = 'demo' | 'real' | 'no_data'   // no_data = no imports yet (onboarding)

// ── causal trust primitives ──
export interface L1Evidence {
  source: string                // "WB import", "finance_snapshot"
  volume: number | string       // "128 orders", "12 товаров"
  period: string                // "last import batch" | "unknown period"
}
export interface L1Mechanism {
  description: string           // causal explanation (one sentence)
  chain: string[]               // step-by-step causal chain
}
export interface L1Confidence {
  level: 'high' | 'medium' | 'low'
  reason: string
}

// STATE layer
export interface L1State { mode: L1Mode; timeframe: string }

// FACT layer
export interface L1MoneyStrip {
  net_profit: number            // primary
  revenue: number
  margin: number                // %
  delta_net: number | null      // null = not available from current API
  evidence: L1Evidence          // grounding — money is never shown ungrounded
}

// INTERPRETATION layer
export interface L1Action {
  label: string                 // verb
  outcome: string               // what changes
  estimated_impact: string      // ₽/% or ''
  target: string                // product / area
  mechanism: string             // HOW it changes the result (causal)
  url: string
  type: 'primary' | 'secondary'
}
export interface L1Candidate { title: string; impact_rub: number; why_not: string }
// Funnel classification — maps the EXISTING insight category (categoryOf(key))
// onto the analytics insight_type enum. Single source: no parallel classifier.
export type InsightType =
  | 'inventory_risk' | 'pricing_issue' | 'ad_loss'
  | 'competitor_signal' | 'promotion_issue' | 'profit_leak' | 'other'

const CATEGORY_TO_INSIGHT_TYPE: Record<string, InsightType> = {
  high_ad_spend:   'ad_loss',
  margin_crisis:   'profit_leak',
  low_stock:       'inventory_risk',
  out_of_stock:    'inventory_risk',
  seo_opportunity: 'other',
  price:           'pricing_issue',
  pricing:         'pricing_issue',
  competitor:      'competitor_signal',
  promo:           'promotion_issue',
  promotion:       'promotion_issue',
  auto_promo:      'promotion_issue',
}

export function insightTypeOf(key: string): InsightType {
  return CATEGORY_TO_INSIGHT_TYPE[categoryOf(key)] ?? 'other'
}

export interface L1Problem {
  title: string
  impact_rub: number
  insight_type: InsightType    // funnel classification (analytics)
  reason: string
  selection_reason: string      // why THIS problem vs others
  competing: L1Candidate[]      // top runners-up
  causal_mechanism: L1Mechanism // how the metric loses money
  evidence: L1Evidence          // where the data comes from
  confidence: L1Confidence      // how much to trust this
  actions: L1Action[]
  is_demo: boolean
}
export type Bucket = 'leak' | 'gain'
export interface L1ProductLine { product: string; margin: number; effect_rub: number; bucket: Bucket }

export interface L1Decision {
  state: L1State
  money_strip: L1MoneyStrip
  problem: L1Problem | null     // null → "Срочных проблем нет"
  leaks: L1ProductLine[]
  gains: L1ProductLine[]
}

// ── V5 progressive-disclosure contract — split one decision into 3 layers ────
// DEFAULT = what shows always (decision in <5s). EXPANDED = trust depth on
// demand. AUDIT = raw, for debug/pro. selectL1 still produces the full
// L1Decision; splitL1 is a pure view over it (no recompute, no logic change).
export interface L1Default {
  state: L1State
  money_strip: { net_profit: number; revenue: number; margin: number }  // FACT only
  problem: { title: string; impact_rub: number; primary_action: { label: string; url: string } | null } | null
  has_depth: boolean            // hint: an EXPAND view exists
}
export interface L1Expanded {
  reason: string
  selection_reason: string
  competing: L1Candidate[]
  causal_mechanism: L1Mechanism
  confidence: L1Confidence
  evidence: L1Evidence
  secondary_actions: L1Action[] // actions beyond the primary CTA
  leaks: L1ProductLine[]
  gains: L1ProductLine[]
}
export interface L1View { default: L1Default; expanded: L1Expanded | null }

export function splitL1(d: L1Decision): L1View {
  const p = d.problem
  const primary = p?.actions.find(a => a.type === 'primary') ?? p?.actions[0] ?? null
  return {
    default: {
      state: d.state,
      money_strip: { net_profit: d.money_strip.net_profit, revenue: d.money_strip.revenue, margin: d.money_strip.margin },
      problem: p ? {
        title: p.title, impact_rub: p.impact_rub,
        primary_action: primary ? { label: primary.label, url: primary.url } : null,
      } : null,
      has_depth: !!p || d.leaks.length > 0 || d.gains.length > 0,
    },
    expanded: p ? {
      reason: p.reason,
      selection_reason: p.selection_reason,
      competing: p.competing,
      causal_mechanism: p.causal_mechanism,
      confidence: p.confidence,
      evidence: p.evidence,
      secondary_actions: p.actions.filter(a => a.type !== 'primary'),
      leaks: d.leaks,
      gains: d.gains,
    } : null,
  }
}

const HEALTHY_MARGIN = 10
const UNKNOWN_PERIOD = 'период не определён'

function rub(n: number): string { return `${Math.round(n).toLocaleString('ru-RU')} ₽` }
function impactOf(i: InsightItem): number { return Math.round(i.estimated_monthly_loss_rub ?? 0) }
function categoryOf(key: string): string {
  const base = (key || '').split(':')[0]
  return base.startsWith('demo_') ? base.slice(5) : base
}

// category → causal mechanism (how the metric loses money). Deterministic map.
const PROBLEM_MECHANISM: Record<string, L1Mechanism> = {
  high_ad_spend:   { description: 'Реклама растёт быстрее выручки, каждый рубль возвращает меньше — маржа сжимается.',
                     chain: ['ДРР растёт', 'реклама не окупается', 'сжатие маржи', 'падение чистой прибыли'] },
  margin_crisis:   { description: 'Себестоимость и комиссии съедают цену — товар идёт с минимальной/отрицательной маржой.',
                     chain: ['издержки выше нормы', 'маржа ниже категории', 'убыток на единице', 'потеря прибыли'] },
  low_stock:       { description: 'Низкий остаток ведёт к out-of-stock, потере позиций в поиске и продаж.',
                     chain: ['низкий остаток', 'out-of-stock', 'падение позиций', 'потеря продаж'] },
  seo_opportunity: { description: 'Низкий CTR карточки тянет органику вниз и повышает зависимость от платной рекламы.',
                     chain: ['низкий CTR', 'мало органики', 'рост рекламной нагрузки', 'рост издержек'] },
}
const DEFAULT_MECHANISM: L1Mechanism = {
  description: 'Сигнал влияет на финансовый результат товара.',
  chain: ['отклонение метрики', 'финансовый эффект'],
}

// url → verb + business outcome + causal mechanism (only known L3 routes)
const ACTION_META: Record<string, { verb: string; outcome: string; mechanism: string }> = {
  '/ad-strategy':       { verb: 'Снизить рекламу',          outcome: 'снять давление на маржу',    mechanism: 'снижение ставок → меньше CAC → реклама окупается → растёт маржа' },
  '/dashboard/seo':     { verb: 'Пересобрать карточку',     outcome: 'поднять CTR и продажи',      mechanism: 'новая карточка → выше CTR → больше органики → ниже зависимость от рекламы' },
  '/dashboard/data':    { verb: 'Проверить экономику',      outcome: 'найти источник потери',      mechanism: 'разбор затрат → находим статью потери → корректируем → восстанавливается маржа' },
  '/suppliers':         { verb: 'Пополнить склад',          outcome: 'сохранить позиции в поиске', mechanism: 'пополнение → нет out-of-stock → сохраняются позиции → не теряем продажи' },
  '/logistics':         { verb: 'Оптимизировать логистику', outcome: 'снизить издержки доставки',  mechanism: 'оптимизация упаковки/схемы → ниже стоимость доставки → растёт маржа' },
  '/dashboard/monitor': { verb: 'Отслеживать',              outcome: 'контролировать динамику',    mechanism: 'наблюдение → раннее обнаружение отклонений → своевременная реакция' },
}

function buildActions(p: InsightItem): L1Action[] {
  const target = p.product_name ?? p.subtitle ?? ''
  const impactRub = impactOf(p)
  return (p.actions ?? []).slice(0, 3).map((a, idx) => {
    const meta = ACTION_META[a.url]
    return {
      label: meta?.verb ?? a.label,                        // verb, not nav label
      outcome: meta?.outcome ?? '',
      estimated_impact: idx === 0 && impactRub > 0 ? `вернуть до ${rub(impactRub)}/мес` : '',
      target,
      mechanism: meta?.mechanism ?? '',                    // HOW the action works
      url: a.url,
      type: idx === 0 ? 'primary' : 'secondary',
    }
  })
}

// Reuse the REAL backend confidence. Demo or zero-coverage → forced low (never overstate).
function confidenceOf(p: InsightItem, coverage: number): L1Confidence {
  if (p.is_demo) return { level: 'low', reason: 'демо-данные — пример, не ваш бизнес' }
  const pct = Math.round((p.confidence ?? 0) * 100)
  if (coverage === 0) return { level: 'low', reason: 'нет загруженных финансовых данных' }
  const level = p.confidence_level ?? (coverage >= 3 ? 'medium' : 'low')
  return { level, reason: `достоверность ${pct}% по ${coverage} товарам` }
}

function problemEvidence(p: InsightItem, coverage: number): L1Evidence {
  return {
    source: `${p.marketplace || 'маркетплейс'} · импорт/финансы`,
    volume: coverage > 0 ? `${coverage} товаров` : 'нет загруженных товаров',
    period: UNKNOWN_PERIOD,                  // honest: insight DTO carries no period
  }
}

export function selectL1(insights: InsightItem[], finance: FinanceSummaryItem[], hasData: boolean = true): L1Decision {
  const coverage = finance.length
  const periods = Math.max(0, ...finance.map(p => p.snapshots_count ?? 0))
  const timeframe = periods > 1 ? `за ${periods} периода` : 'за весь загруженный период'

  const ranked = insights
    .filter(i => i.type === 'warning')
    .slice()
    .sort((a, b) =>
      ((b.impact_score ?? 0) - (a.impact_score ?? 0)) ||
      (impactOf(b) - impactOf(a)))

  const top = ranked[0]

  const problem: L1Problem | null = top ? {
    title: top.title,
    impact_rub: impactOf(top),
    insight_type: insightTypeOf(top.key),
    reason: top.reasons?.[0] ?? '',
    selection_reason: ranked.length > 1
      ? `Наибольший финансовый эффект (−${rub(impactOf(top))}/мес) среди ${ranked.length} активных сигналов.`
      : 'Единственный активный сигнал.',
    competing: ranked.slice(1, 4).map(c => ({
      title: c.title,
      impact_rub: impactOf(c),
      why_not: `−${rub(impactOf(c))}/мес — ниже выбранного`,
    })),
    causal_mechanism: PROBLEM_MECHANISM[categoryOf(top.key)] ?? DEFAULT_MECHANISM,
    evidence: problemEvidence(top, coverage),
    confidence: confidenceOf(top, coverage),
    actions: buildActions(top),
    is_demo: top.is_demo,
  } : null

  // STATE — no_data (no imports) > demo > real. no_data drives onboarding UI.
  const isDemo = problem ? problem.is_demo : insights.some(i => i.is_demo)
  const mode: L1Mode = !hasData ? 'no_data' : (isDemo ? 'demo' : 'real')

  // FACT — money strip (net primary), grounded with evidence. No period delta in API.
  const net     = finance.reduce((s, p) => s + p.total_net_profit, 0)
  const revenue = finance.reduce((s, p) => s + p.total_revenue, 0)
  const money_strip: L1MoneyStrip = {
    net_profit: Math.round(net),
    revenue: Math.round(revenue),
    margin: revenue > 0 ? +((net / revenue) * 100).toFixed(1) : 0,
    delta_net: null,
    evidence: { source: 'Финансовая сводка (импорт)', volume: `${coverage} товаров`, period: timeframe },
  }

  // INTERPRETATION — mutually exclusive buckets (one product → one bucket)
  const leaks: L1ProductLine[] = []
  const gains: L1ProductLine[] = []
  for (const p of finance) {
    const line: L1ProductLine = {
      product: p.product_name,
      margin: +p.avg_margin_percent.toFixed(1),
      effect_rub: Math.round(p.total_net_profit),
      bucket: (p.total_net_profit < 0 || p.avg_margin_percent < HEALTHY_MARGIN) ? 'leak' : 'gain',
    }
    if (line.bucket === 'leak') leaks.push(line); else gains.push(line)
  }
  leaks.sort((a, b) => a.margin - b.margin)
  gains.sort((a, b) => b.effect_rub - a.effect_rub)

  return {
    state: { mode, timeframe },
    money_strip,
    problem,
    leaks: leaks.slice(0, 5),
    gains: gains.slice(0, 5),
  }
}

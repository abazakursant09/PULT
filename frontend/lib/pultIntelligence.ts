// ── Product Intelligence Engine — candidate prep for L1 ──────────────────────
// Transforms raw business data into the structured input the L1 selector
// (selectL1) consumes. Does NOT decide the dominant problem — only prepares
// candidates. Pure, deterministic. Actions map only to allowed L3 routes.

export interface RawInsight {
  type: 'warning' | 'info'
  title: string
  severity?: number                       // 0..1
  impact_score?: number                   // 0..1 (may be absent)
  estimated_monthly_loss_rub?: number
  signals?: string[]
}
export interface RawProduct {
  name: string
  revenue: number
  net_profit: number
  margin_percent: number
}
export interface RawFinanceSummary {
  total_profit: number
  total_revenue: number
  total_margin_percent: number
}

export interface PreparedAction { label: string; url: string; priority: number }
// evidence grounding — where each number comes from (no ungrounded figures)
export interface PreparedEvidence { source: string; volume: number | string; period: string }
export interface PreparedInsight {
  title: string
  impact_score: number
  estimated_monthly_loss_rub: number
  actions: PreparedAction[]
  evidence: PreparedEvidence
}
export interface PreparedProductLine { product: string; margin: number; net_profit: number }
export interface L1Candidates {
  normalized_insights: PreparedInsight[]
  finance_ready: { profit: number; revenue: number; margin: number; evidence: PreparedEvidence }
  leaks: PreparedProductLine[]
  gains: PreparedProductLine[]
}

const UNKNOWN_PERIOD = 'период не определён'

// Only these L3 routes may appear in actions. Nothing else is allowed.
const L3_ROUTES = new Set([
  '/ad-strategy', '/dashboard/seo', '/dashboard/data',
  '/suppliers', '/logistics', '/ai-agents', '/dashboard/monitor',
])

// keyword → primary L3 route (deterministic mapping, no new routes)
const ROUTE_MAP: Array<[RegExp, string, string]> = [
  [/реклам|дрр|\bads?\b|буст|продвиж/i, '/ad-strategy',       'Снизить рекламную нагрузку'],
  [/карточк|ctr|seo|конверс|видим/i,    '/dashboard/seo',      'Пересобрать карточку'],
  [/маржа|цена|затрат|комисс|себестоим/i,'/dashboard/data',    'Проверить экономику товара'],
  [/остаток|склад|out.?of.?stock|поставк/i,'/suppliers',       'Пополнить склад'],
  [/логистик|доставк|отгрузк/i,         '/logistics',          'Оптимизировать логистику'],
]

function clamp01(n: number): number { return Math.max(0, Math.min(1, n)) }

function buildActions(ins: RawInsight): PreparedAction[] {
  const hay = `${ins.title} ${(ins.signals ?? []).join(' ')}`
  const out: PreparedAction[] = []
  for (const [re, url, label] of ROUTE_MAP) {
    if (re.test(hay)) { out.push({ label, url, priority: 1 }); break }
  }
  if (out.length === 0) out.push({ label: 'Открыть данные', url: '/dashboard/data', priority: 1 })
  // secondary: always allow checking facts + tracking the change (L3 only)
  out.push({ label: 'Проверить факты', url: '/dashboard/data', priority: 2 })
  out.push({ label: 'Отслеживать изменение', url: '/dashboard/monitor', priority: 3 })
  // dedup by url, keep order, cap 3, enforce allowed routes
  const seen = new Set<string>()
  return out.filter(a => L3_ROUTES.has(a.url) && !seen.has(a.url) && seen.add(a.url)).slice(0, 3)
}

function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0
  const s = [...values].sort((a, b) => a - b)
  const idx = Math.min(s.length - 1, Math.floor((p / 100) * s.length))
  return s[idx]
}

export function prepareL1Candidates(
  insights: RawInsight[],
  products: RawProduct[],
  finance: RawFinanceSummary,
): L1Candidates {
  const maxLoss = Math.max(1, ...insights.map(i => i.estimated_monthly_loss_rub ?? 0))

  // 1. Normalize insights (warnings only — info are not L1 candidates)
  const normalized_insights: PreparedInsight[] = insights
    .filter(i => i.type === 'warning')
    .map(i => {
      const proxy = (i.estimated_monthly_loss_rub ?? 0) / maxLoss
      const score = i.impact_score != null
        ? clamp01(i.impact_score)
        : clamp01(((i.severity ?? 0) + proxy) / 2)         // 1. derive when absent
      return {
        title: i.title,
        impact_score: +score.toFixed(3),
        estimated_monthly_loss_rub: Math.round(i.estimated_monthly_loss_rub ?? 0),
        actions: buildActions(i),                          // 2. ≤3 L3-only actions
        evidence: {                                        // 6. grounding injection
          source: 'импорт / финансовая сводка',
          volume: `${products.length} товаров`,
          period: UNKNOWN_PERIOD,                          // honest: no period in input
        },
      }
    })
    // 5. order for the decision engine: impact desc, then ₽ loss desc
    .sort((a, b) =>
      (b.impact_score - a.impact_score) ||
      (b.estimated_monthly_loss_rub - a.estimated_monthly_loss_rub))

  // 4. Money strip — only from finance_summary, no guessing
  const finance_ready = {
    profit: Math.round(finance.total_profit),
    revenue: Math.round(finance.total_revenue),
    margin: +finance.total_margin_percent.toFixed(1),
    evidence: { source: 'финансовая сводка', volume: `${products.length} товаров`, period: UNKNOWN_PERIOD },
  }

  // 3. Financial segmentation
  const p70 = percentile(products.filter(p => p.net_profit > 0).map(p => p.net_profit), 70)
  const line = (p: RawProduct): PreparedProductLine => ({
    product: p.name, margin: +p.margin_percent.toFixed(1), net_profit: Math.round(p.net_profit),
  })
  const leaks = products.filter(p => p.margin_percent < 10 || p.net_profit < 0)
    .sort((a, b) => a.margin_percent - b.margin_percent).slice(0, 5).map(line)
  const gains = products.filter(p => p.net_profit > 0 && p.net_profit >= p70)
    .sort((a, b) => b.net_profit - a.net_profit).slice(0, 5).map(line)

  return { normalized_insights, finance_ready, leaks, gains }
}

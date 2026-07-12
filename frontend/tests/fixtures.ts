import type { PresentationCard, TodaySummary } from '@/lib/api'

// Fixtures shaped EXACTLY like the real API responses (lib/api.ts). If the backend contract
// moves, these stop type-checking — which is the point: a silent shape drift between
// backend and frontend is precisely the failure this suite exists to catch.

export const todayWithData: TodaySummary = {
  revenue_today: 128_400,
  profit_today: -3_200,
  margin_pct: -2.5,
  delta_revenue_pct: -12,
  critical_count: 2,
  loss_products_count: 3,
  growth_opportunities_count: 1,
  low_stock_count: 4,
  is_demo: false,
  has_data: true,
}

export const todayNoData: TodaySummary = {
  ...todayWithData,
  revenue_today: 0,
  profit_today: 0,
  margin_pct: null,
  delta_revenue_pct: null,
  critical_count: 0,
  loss_products_count: 0,
  growth_opportunities_count: 0,
  low_stock_count: 0,
  has_data: false,
}

// A second, distinct diagnosis. The dashboard hands the feed `skipTopAction`, so the first
// card is intentionally withheld there (TodayFocus owns it) — proving the feed still renders
// requires a card that is NOT the top one.
export const secondCard: PresentationCard = {
  marketplace: 'ozon',
  sku: 'ART-2002',
  group_key: 'ozon:ART-2002',
  highest_severity: 'warning',
  contributing_contours: ['supply'],
  recommendations: ['Пополнить остаток'],
  evidence: [{ metric: 'days_of_stock', value: 4 }],
  recommendation_groups: [],
  root_cause_narrative: 'Остаток кончится через 4 дня при текущей скорости продаж.',
  items: [
    {
      item_key: 'supply:ART-2002',
      contour: 'supply',
      source_table: 'supply_signal',
      source_id: 'sig-2',
      source_status: 'open',
      attention_state: 'new',
      marketplace: 'ozon',
      sku: 'ART-2002',
      title: 'Скоро закончится остаток',
      what_happened: 'Остатка хватит на 4 дня',
      why_it_matters: 'Продажи остановятся',
      meaning: null,
    } as PresentationCard['items'][number],
  ],
}

export const diagnosisCard: PresentationCard = {
  marketplace: 'wildberries',
  sku: 'ART-1001',
  group_key: 'wildberries:ART-1001',
  highest_severity: 'critical',
  contributing_contours: ['money_leak', 'pricing'],
  recommendations: ['Снизить рекламный бюджет'],
  evidence: [{ metric: 'ad_cost_ratio', value: 0.41 }],
  recommendation_groups: [],
  root_cause_narrative: 'Реклама съедает маржу: доля рекламных расходов выросла до 41%.',
  items: [
    {
      item_key: 'money_leak:ART-1001',
      contour: 'money_leak',
      source_table: 'money_leak_signal',
      source_id: 'sig-1',
      source_status: 'open',
      attention_state: 'new',
      marketplace: 'wildberries',
      sku: 'ART-1001',
      title: 'Утечка маржи',
      what_happened: 'Доля рекламных расходов выросла до 41%',
      why_it_matters: 'Товар уходит в минус',
      meaning: null,
    } as PresentationCard['items'][number],
  ],
}

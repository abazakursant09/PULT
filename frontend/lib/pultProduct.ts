/**
 * ПУЛЬТ V2 — товар-центричный слой данных.
 *
 * Принцип: товар = атом. Любая аналитика = линза товара.
 * Все 6 вкладок Кабинета читают ОДИН источник `ProductWithLenses[]`,
 * фильтруя по набору линз. UI не знает про backend — только про этот тип.
 *
 * Ingest-готовность: сейчас отдаётся mock. Чтобы включить реальные данные —
 * реализовать `selectProductLenses(raw)` поверх api (products/finance/reviews/
 * pricing/legal/seoCards) и заменить `mockProducts()` на него. UI не меняется.
 */

export type Mode = 'real' | 'demo' | 'empty'

// Severity → семантика. Цвет берётся из CSS-vars в компонентах (severity.ts).
export type Severity = 'red' | 'amber' | 'green'

// Линзы товара (canon). Группы определяют, на какой вкладке линза появляется.
export type LensKey =
  | 'pribyl' | 'reklama' | 'cena' | 'akcii' | 'seo' | 'otzyvy'   // товар / возможности / съедает
  | 'komissiya' | 'vozvraty' | 'logistika'                       // съедает
  | 'dok' | 'brand' | 'jaloby' | 'block'                         // риски

export interface LensMeta { key: LensKey; label: string; icon: string }

export const LENS: Record<LensKey, LensMeta> = {
  pribyl:    { key: 'pribyl',    label: 'Прибыль',    icon: '₽' },
  reklama:   { key: 'reklama',   label: 'Реклама',    icon: '◈' },
  cena:      { key: 'cena',      label: 'Цена',       icon: '⌁' },
  akcii:     { key: 'akcii',     label: 'Акции',      icon: '◷' },
  seo:       { key: 'seo',       label: 'SEO',        icon: '⌕' },
  otzyvy:    { key: 'otzyvy',    label: 'Отзывы',     icon: '★' },
  komissiya: { key: 'komissiya', label: 'Комиссия',  icon: '%' },
  vozvraty:  { key: 'vozvraty',  label: 'Возвраты',  icon: '↩' },
  logistika: { key: 'logistika', label: 'Логистика', icon: '⛟' },
  dok:       { key: 'dok',       label: 'Документы', icon: '▤' },
  brand:     { key: 'brand',     label: 'Бренд',      icon: '™' },
  jaloby:    { key: 'jaloby',    label: 'Жалобы',     icon: '!' },
  block:     { key: 'block',     label: 'Блокировки', icon: '⛔' },
}

// Наборы линз по вкладкам — единственное место, где задаётся «что куда».
export const LEAK_LENSES: LensKey[] = ['reklama', 'komissiya', 'akcii', 'vozvraty', 'logistika']
export const RISK_LENSES: LensKey[] = ['dok', 'brand', 'jaloby', 'block']
export const GROWTH_LENSES: LensKey[] = ['cena', 'seo', 'otzyvy']
// Линзы, показываемые чипами в карточке товара (Товары)
export const CARD_LENSES: LensKey[] = ['reklama', 'cena', 'otzyvy', 'seo', 'dok']

/**
 * Табы карточки товара /products/[id] (LensTabs). Один товар по линзам.
 * single — линза рендерит свой LensSignal. group — несколько линз (Риски).
 * pribyl — спец-таб P&L (не сигнал). yurist — только документы.
 */
export interface ProductTab { key: string; label: string; lenses: LensKey[]; pnl?: boolean }
export const PRODUCT_TABS: ProductTab[] = [
  { key: 'pribyl',  label: 'Прибыль', lenses: [], pnl: true },
  { key: 'reklama', label: 'Реклама', lenses: ['reklama'] },
  { key: 'cena',    label: 'Цены',    lenses: ['cena'] },
  { key: 'akcii',   label: 'Акции',   lenses: ['akcii'] },
  { key: 'seo',     label: 'SEO',     lenses: ['seo'] },
  { key: 'otzyvy',  label: 'Отзывы',  lenses: ['otzyvy'] },
  { key: 'riski',   label: 'Риски',   lenses: RISK_LENSES },
  { key: 'yurist',  label: 'Юрист',   lenses: ['dok'] },
]

/** Найти товар по id (из текущего источника). */
export function productById(products: ProductWithLenses[], id: string): ProductWithLenses | undefined {
  return products.find(p => p.id === id)
}

/** Сигнал линзы = формула «Проблема → Причина → Решение → Эффект». */
export interface LensSignal {
  severity: Severity
  problem: string          // ЧТО случилось (₽, не %)
  cause: string            // ПОЧЕМУ
  solution: string         // ЧТО делать
  effect_rub: number       // ЧТО получу, ₽/мес (>0 эффект решения)
  effect_text?: string     // человекочитаемый эффект (ДРР 41%→18% и т.п.)
  insightKey?: string      // ключ для api.actionEngine.executeInsight (Проверить/Выполнить)
  auto: boolean            // доступна ли автоматизация
  badge?: 'csv' | 'demo'   // источник данных, если не общий режим
}

export interface ProductWithLenses {
  id: string
  name: string
  photo: string            // emoji-плейсхолдер; в проде — URL
  mp: string               // «WB · #3»
  rating: number
  profit: number           // ₽/мес, знак = здоровье
  status: 'ok' | 'warn' | 'risk'
  lenses: Partial<Record<LensKey, LensSignal>>
}

const SEV_RANK: Record<Severity, number> = { red: 0, amber: 1, green: 2 }

/** Худшая линза товара = «главная проблема» / цель кнопки «Решить главное». */
export function worstLens(p: ProductWithLenses): LensKey | null {
  const keys = Object.keys(p.lenses) as LensKey[]
  if (!keys.length) return null
  return keys.sort((a, b) =>
    SEV_RANK[p.lenses[a]!.severity] - SEV_RANK[p.lenses[b]!.severity]
    || p.lenses[a]!.effect_rub - p.lenses[b]!.effect_rub
  )[0]
}

export interface FlatSignal { product: ProductWithLenses; lens: LensKey; signal: LensSignal }

/** Разложить товары в плоский список (товар × линза) по набору линз. */
export function flatten(products: ProductWithLenses[], lenses: LensKey[]): FlatSignal[] {
  const out: FlatSignal[] = []
  for (const product of products) {
    for (const lens of lenses) {
      const signal = product.lenses[lens]
      if (signal) out.push({ product, lens, signal })
    }
  }
  return out.sort((a, b) =>
    SEV_RANK[a.signal.severity] - SEV_RANK[b.signal.severity]
    || a.signal.effect_rub - b.signal.effect_rub
  )
}

/** Сумма потерь по набору линз (для шапки «Под угрозой: −X ₽»). */
export function sumLoss(products: ProductWithLenses[], lenses: LensKey[]): number {
  return flatten(products, lenses)
    .filter(f => f.signal.severity !== 'green')
    .reduce((a, f) => a + Math.abs(f.signal.effect_rub), 0)
}

/** Сумма потенциала роста (для шапки «Потенциал: +X ₽»). */
export function sumGrowth(products: ProductWithLenses[]): number {
  return flatten(products, GROWTH_LENSES)
    .filter(f => f.signal.effect_rub > 0)
    .reduce((a, f) => a + f.signal.effect_rub, 0)
}

/** Главный сигнал дня для Кабинета: самый дорогой по модулю эффекта. */
export function mainSignal(products: ProductWithLenses[]): FlatSignal | null {
  const all = flatten(products, [...LEAK_LENSES, ...GROWTH_LENSES, ...RISK_LENSES])
  if (!all.length) return null
  return all.slice().sort((a, b) => Math.abs(b.signal.effect_rub) - Math.abs(a.signal.effect_rub))[0]
}

// ── MONEY (3 периода) ───────────────────────────────────────────────────────
export interface Money { today: number; d7: number; d30: number; d30Delta: number }

export function moneyOf(mode: Mode): Money {
  if (mode === 'empty') return { today: 0, d7: 0, d30: 0, d30Delta: 0 }
  return { today: 9_840, d7: 61_200, d30: 248_600, d30Delta: 6.2 }
}

export const fmtRub = (n: number) => `${Math.round(n).toLocaleString('ru-RU')} ₽`

// ── MOCK / DEMO данные ──────────────────────────────────────────────────────
// retention-first: «Реклама» и «Отзывы» наполнены плотнее, с insightKey.
function mockProducts(): ProductWithLenses[] {
  return [
    {
      id: 'blend', name: 'Блендер PowerBlend 1200 Вт', photo: '🥤', mp: 'WB · #3',
      rating: 4.7, profit: -38_400, status: 'risk',
      lenses: {
        reklama: { severity: 'red', problem: 'Теряете 38 400 ₽/мес на рекламе',
          cause: 'CPM ×2.3 за 14 дней, конверсия та же — ставка выше рынка',
          solution: 'Снизить ставку CPM 480 → 210 ₽', effect_rub: 31_200,
          effect_text: 'ДРР 41% → 18%', insightKey: 'high_ad_spend', auto: true },
        otzyvy: { severity: 'amber', problem: '12 отзывов без ответа',
          cause: 'Снижает доверие карточки на 4%', solution: 'Авто-ответы на нейтральные',
          effect_rub: 6_800, effect_text: '+4% конверсии', insightKey: 'reviews_unanswered', auto: true },
        komissiya: { severity: 'amber', problem: 'Теряете 4 200 ₽/мес на комиссии',
          cause: 'Сменилась категория тарифа МП', solution: 'Проверить категорию товара',
          effect_rub: 4_200, auto: false },
        dok: { severity: 'red', problem: 'Риск блокировки карточки',
          cause: 'Нет сертификата ЕАС — категория требует декларацию',
          solution: 'Загрузить документ соответствия', effect_rub: 0,
          effect_text: 'снятие риска блокировки', auto: false, badge: 'csv' },
      },
    },
    {
      id: 'grind', name: 'Кофемолка GrindPro', photo: '☕', mp: 'Ozon · #14',
      rating: 4.3, profit: 6_100, status: 'warn',
      lenses: {
        otzyvy: { severity: 'red', problem: 'Рейтинг 4.8 → 4.3 за 12 дней',
          cause: '3 негатива «грубый помол» — дефект партии, уходит из топа',
          solution: 'Ответить шаблоном + заявка поставщику', effect_rub: 9_400,
          effect_text: 'стоп падения рейтинга', insightKey: 'rating_drop', auto: true },
        seo: { severity: 'amber', problem: 'Упущено ~37 000 ₽/мес трафика',
          cause: 'Позиция 18 вместо 6 — нет ключа «жернова» в заголовке',
          solution: 'Обновить SEO-заголовок с ключами', effect_rub: 37_000,
          effect_text: '+6 позиций, +9% показов', insightKey: 'seo_opportunity', auto: true },
        akcii: { severity: 'amber', problem: 'Акция съедает 8 100 ₽/мес',
          cause: 'Скидка 25% при марже 14% ≈ ноль прибыли',
          solution: 'Выйти из акции или поднять базу', effect_rub: 8_100, auto: false, badge: 'demo' },
        vozvraty: { severity: 'amber', problem: 'Возвраты съедают 5 800 ₽/мес',
          cause: 'Жалобы на помол → возвраты 9%', solution: 'Связать с дефектом партии',
          effect_rub: 5_800, auto: false },
      },
    },
    {
      id: 'boil', name: 'Чайник FastBoil 1.7 л', photo: '🫖', mp: 'WB · #1',
      rating: 4.9, profit: 54_200, status: 'ok',
      lenses: {
        cena: { severity: 'green', problem: 'Недозарабатываете 12 800 ₽/мес',
          cause: 'Цена ниже рынка на 8%, спрос неэластичен',
          solution: 'Поднять цену 1990 → 2110 ₽', effect_rub: 12_800,
          effect_text: 'без потери заказов', insightKey: 'price_up', auto: true },
        reklama: { severity: 'green', problem: 'Реклама окупается',
          cause: 'ДРР 7%, конверсия 14%', solution: 'Масштабировать бюджет +20%',
          effect_rub: 11_400, effect_text: '+заказы при той же ДРР', auto: true },
      },
    },
  ]
}

/**
 * Точка входа для UI. Сейчас — mock по режиму.
 * TODO ingest: заменить тело на сборку из api.* (см. шапку файла).
 */
export function getProducts(mode: Mode): ProductWithLenses[] {
  if (mode === 'empty') return []
  return mockProducts()
}

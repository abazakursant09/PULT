'use client'

import { useState, useMemo, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { api, type ImportFinanceSummary } from '@/lib/api'
import { TrendingUp, TrendingDown, Download, Package, AlertTriangle, Trophy } from 'lucide-react'
import { FinanceChart } from '@/components/FinanceChart'
import { ShareSuccessModal } from '@/components/ShareSuccessModal'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { BlurFade } from '@/components/ui/blur-fade'

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(n: number) { return Math.abs(n).toLocaleString('ru-RU') }
function fmtM(n: number) { return `${fmt(n)} ₽` }

// ── Types ─────────────────────────────────────────────────────────────────────
type MP = 'all' | 'wb' | 'ozon' | 'ym'
type PeriodMode = 'months' | 'weeks' | 'days'
type SortDir = 'asc' | 'desc'

interface PeriodRow {
  period: string
  revenue: number
  commissions: number
  logistics: number
  storage: number
  advertising: number
  taxes: number
  netProfit: number
}

interface ProductRow {
  name: string
  mp: MP[]
  revenue: number
  expenses: number
  profit: number
  margin: number
  rating: number
  sales: number
}

type ProductSortKey = keyof Omit<ProductRow, 'mp'>

// ── Mock KPIs ─────────────────────────────────────────────────────────────────
const KPI: Record<MP, {
  revenue: number; revenueD: number
  profit: number;  profitD: number
  margin: number;  marginD: number
  expenses: number; expensesD: number
  taxes: number;   taxesD: number
  adBudget: number; adBudgetD: number
}> = {
  all: { revenue:4_520_000,revenueD:14.2, profit:812_000,profitD:8.7, margin:18.0,marginD:-1.4, expenses:3_708_000,expensesD:16.1, taxes:271_200,taxesD:14.2, adBudget:452_000,adBudgetD:22.5 },
  wb:  { revenue:2_650_000,revenueD:18.3, profit:477_000,profitD:12.1, margin:18.0,marginD:0.8, expenses:2_173_000,expensesD:19.5, taxes:159_000,taxesD:18.3, adBudget:265_000,adBudgetD:28.4 },
  ozon:{ revenue:1_190_000,revenueD:9.4,  profit:226_100,profitD:5.2,  margin:19.0,marginD:-0.9, expenses:963_900,expensesD:10.8,  taxes:71_400,taxesD:9.4,   adBudget:119_000,adBudgetD:15.2 },
  ym:  { revenue:680_000,  revenueD:-3.1, profit:108_800,profitD:-8.4, margin:16.0,marginD:-2.1, expenses:571_200,expensesD:-1.8,  taxes:40_800,taxesD:-3.1,  adBudget:68_000,adBudgetD:-5.0 },
}

// ── Expense Breakdown ─────────────────────────────────────────────────────────
// Сумма сегментов = totalExpenses из KPI (самопроверяемые числа)
const EXPENSES: Record<MP, { label: string; value: number; color: string }[]> = {
  all: [
    { label:'Себестоимость и прочее', value:1_673_900, color:'#4A4A50' },
    { label:'Комиссии',               value:  813_600, color:'#A78BFA' },
    { label:'Реклама',                value:  452_000, color:'#E0C07E' },
    { label:'Логистика',              value:  361_600, color:'#A8833E' },
    { label:'Налоги (УСН 6%)',        value:  271_200, color:'#786040' },
    { label:'Хранение',               value:  135_700, color:'#4A3A28' },
  ],
  wb: [
    { label:'Себестоимость и прочее', value:  980_500, color:'#4A4A50' },
    { label:'Комиссии',               value:  477_000, color:'#A78BFA' },
    { label:'Реклама',                value:  265_000, color:'#E0C07E' },
    { label:'Логистика',              value:  212_000, color:'#A8833E' },
    { label:'Налоги (УСН 6%)',        value:  159_000, color:'#786040' },
    { label:'Хранение',               value:   79_500, color:'#4A3A28' },
  ],
  ozon: [
    { label:'Себестоимость и прочее', value:  428_400, color:'#4A4A50' },
    { label:'Комиссии',               value:  214_200, color:'#A78BFA' },
    { label:'Реклама',                value:  119_000, color:'#E0C07E' },
    { label:'Логистика',              value:   95_200, color:'#A8833E' },
    { label:'Налоги (УСН 6%)',        value:   71_400, color:'#786040' },
    { label:'Хранение',               value:   35_700, color:'#4A3A28' },
  ],
  ym: [
    { label:'Себестоимость и прочее', value:  272_000, color:'#4A4A50' },
    { label:'Комиссии',               value:  122_400, color:'#A78BFA' },
    { label:'Реклама',                value:   68_000, color:'#E0C07E' },
    { label:'Логистика',              value:   47_600, color:'#A8833E' },
    { label:'Налоги (УСН 6%)',        value:   40_800, color:'#786040' },
    { label:'Хранение',               value:   20_400, color:'#4A3A28' },
  ],
}

// ── Period Data ───────────────────────────────────────────────────────────────
function scaleRows(rows: PeriodRow[], k: number): PeriodRow[] {
  return rows.map(r => ({
    ...r,
    revenue:     Math.round(r.revenue     * k),
    commissions: Math.round(r.commissions * k),
    logistics:   Math.round(r.logistics   * k),
    storage:     Math.round(r.storage     * k),
    advertising: Math.round(r.advertising * k),
    taxes:       Math.round(r.taxes       * k),
    netProfit:   Math.round(r.netProfit   * k),
  }))
}

const M_ALL: PeriodRow[] = [
  { period:'Январь 2025',  revenue:3_820_000, commissions:687_600, logistics:305_600, storage:114_600, advertising:382_000, taxes:229_200, netProfit:688_000 },
  { period:'Февраль 2025', revenue:3_940_000, commissions:709_200, logistics:315_200, storage:118_200, advertising:394_000, taxes:236_400, netProfit:720_000 },
  { period:'Март 2025',    revenue:4_110_000, commissions:739_800, logistics:328_800, storage:123_300, advertising:411_000, taxes:246_600, netProfit:742_000 },
  { period:'Апрель 2025',  revenue:4_280_000, commissions:770_400, logistics:342_400, storage:128_400, advertising:428_000, taxes:256_800, netProfit:782_000 },
  { period:'Май 2025',     revenue:4_520_000, commissions:813_600, logistics:361_600, storage:135_700, advertising:452_000, taxes:271_200, netProfit:812_000 },
]

const W_ALL: PeriodRow[] = [
  { period:'Нед. 14 (31 мар – 6 апр)',  revenue:1_040_000, commissions:187_200, logistics:83_200,  storage:31_200, advertising:104_000, taxes:62_400, netProfit:192_000 },
  { period:'Нед. 15 (7 апр – 13 апр)',  revenue:  980_000, commissions:176_400, logistics:78_400,  storage:29_400, advertising: 98_000, taxes:58_800, netProfit:178_000 },
  { period:'Нед. 16 (14 апр – 20 апр)', revenue:1_120_000, commissions:201_600, logistics:89_600,  storage:33_600, advertising:112_000, taxes:67_200, netProfit:198_000 },
  { period:'Нед. 17 (21 апр – 27 апр)', revenue:1_060_000, commissions:190_800, logistics:84_800,  storage:31_800, advertising:106_000, taxes:63_600, netProfit:186_000 },
  { period:'Нед. 18 (28 апр – 4 мая)',  revenue:1_180_000, commissions:212_400, logistics:94_400,  storage:35_400, advertising:118_000, taxes:70_800, netProfit:214_000 },
  { period:'Нед. 19 (5 мая – 11 мая)',  revenue:1_320_000, commissions:237_600, logistics:105_600, storage:39_600, advertising:132_000, taxes:79_200, netProfit:242_000 },
]

const D_ALL: PeriodRow[] = [
  { period:'27 апреля', revenue:145_000, commissions:26_100, logistics:10_150, storage:4_350, advertising:14_500, taxes:8_700,  netProfit:26_500 },
  { period:'28 апреля', revenue:163_000, commissions:29_340, logistics:11_410, storage:4_890, advertising:16_300, taxes:9_780,  netProfit:31_400 },
  { period:'29 апреля', revenue:178_000, commissions:32_040, logistics:12_460, storage:5_340, advertising:17_800, taxes:10_680, netProfit:34_900 },
  { period:'30 апреля', revenue:142_000, commissions:25_560, logistics: 9_940, storage:4_260, advertising:14_200, taxes:8_520,  netProfit:26_200 },
  { period:'1 мая',     revenue: 96_000, commissions:17_280, logistics: 6_720, storage:2_880, advertising: 9_600, taxes:5_760,  netProfit:17_100 },
  { period:'2 мая',     revenue:189_000, commissions:34_020, logistics:13_230, storage:5_670, advertising:18_900, taxes:11_340, netProfit:36_900 },
  { period:'3 мая',     revenue:201_000, commissions:36_180, logistics:14_070, storage:6_030, advertising:20_100, taxes:12_060, netProfit:39_000 },
]

const PERIOD: Record<MP, Record<PeriodMode, PeriodRow[]>> = {
  all:  { months:M_ALL,                    weeks:W_ALL,                    days:D_ALL                    },
  wb:   { months:scaleRows(M_ALL,0.586),   weeks:scaleRows(W_ALL,0.586),   days:scaleRows(D_ALL,0.586)   },
  ozon: { months:scaleRows(M_ALL,0.263),   weeks:scaleRows(W_ALL,0.263),   days:scaleRows(D_ALL,0.263)   },
  ym:   { months:scaleRows(M_ALL,0.151),   weeks:scaleRows(W_ALL,0.151),   days:scaleRows(D_ALL,0.151)   },
}

// ── Products ──────────────────────────────────────────────────────────────────
const PRODUCTS: ProductRow[] = [
  { name:'Крем для рук увлажняющий',       mp:['wb','ozon'],      revenue:842_000, expenses:588_000, profit:254_000,  margin: 30.2, rating:4.8, sales:1240 },
  { name:'Шампунь с кератином 500 мл',     mp:['wb'],             revenue:650_000, expenses:494_000, profit:156_000,  margin: 24.0, rating:4.7, sales: 980 },
  { name:'Маска для лица антивозрастная',  mp:['ozon','ym'],      revenue:580_000, expenses:406_000, profit:174_000,  margin: 30.0, rating:4.9, sales: 720 },
  { name:'Сыворотка с витамином C',        mp:['wb','ozon','ym'], revenue:510_000, expenses:397_800, profit:112_200,  margin: 22.0, rating:4.6, sales: 630 },
  { name:'Тоник очищающий 200 мл',         mp:['wb'],             revenue:390_000, expenses:331_500, profit: 58_500,  margin: 15.0, rating:4.4, sales: 820 },
  { name:'Скраб для тела с кофе',          mp:['ozon'],           revenue:280_000, expenses:313_600, profit:-33_600,  margin:-12.0, rating:4.2, sales: 420 },
  { name:'Лосьон для тела 300 мл',         mp:['wb','ym'],        revenue:430_000, expenses:325_400, profit:104_600,  margin: 24.3, rating:4.5, sales: 610 },
  { name:'Патчи для глаз коллагеновые',    mp:['wb','ozon'],      revenue:620_000, expenses:390_600, profit:229_400,  margin: 37.0, rating:4.9, sales:1850 },
  { name:'Бальзам для губ с маслом ши',    mp:['ym'],             revenue: 95_000, expenses:118_750, profit:-23_750,  margin:-25.0, rating:4.1, sales: 380 },
  { name:'Гель для умывания 150 мл',       mp:['wb'],             revenue:320_000, expenses:288_000, profit: 32_000,  margin: 10.0, rating:4.3, sales: 540 },
]

// ── Tabs & columns config ─────────────────────────────────────────────────────
const TABS: { id: MP; label: string }[] = [
  { id:'all', label:'Все площадки'  },
  { id:'wb',  label:'Wildberries'   },
  { id:'ozon',label:'Ozon'          },
  { id:'ym',  label:'Яндекс Маркет' },
]

const PERIOD_HEADERS = ['Период','Выручка','Комиссии','Логистика','Хранение','Реклама','Налоги','Чистая прибыль','Маржа']

const PROD_COLS: { key: ProductSortKey; label: string; right: boolean }[] = [
  { key:'name',     label:'Товар',    right:false },
  { key:'revenue',  label:'Выручка',  right:true  },
  { key:'expenses', label:'Расходы',  right:true  },
  { key:'profit',   label:'Прибыль',  right:true  },
  { key:'margin',   label:'Маржа %',  right:true  },
  { key:'rating',   label:'Рейтинг',  right:true  },
  { key:'sales',    label:'Продажи',  right:true  },
]

// ── Component ─────────────────────────────────────────────────────────────────
export default function FinancePage() {
  const router = useRouter()
  const [mp,         setMp]         = useState<MP>('all')
  const [pmode,      setPmode]      = useState<PeriodMode>('months')
  const [sortKey,    setSortKey]    = useState<ProductSortKey>('revenue')
  const [sortDir,    setSortDir]    = useState<SortDir>('desc')
  const [showShare,  setShowShare]  = useState(false)
  const [importData, setImportData] = useState<ImportFinanceSummary | null>(null)

  useEffect(() => {
    api.csvImport.financeSummary().then(setImportData).catch(() => {})
  }, [])

  // Real data overrides — use imported data when available
  const realMpData = importData?.has_data
    ? (mp === 'all'
        ? { revenue: importData.total_revenue, profit: importData.total_profit,
            commission: importData.total_commission, logistics: importData.total_logistics,
            ad_spend: importData.total_ad_spend, margin: importData.margin_percent }
        : importData.by_marketplace[mp] ?? null)
    : null

  const kpi = realMpData
    ? {
        revenue:    realMpData.revenue,                          revenueD:  0,
        profit:     realMpData.profit,                           profitD:   0,
        margin:     realMpData.margin,                           marginD:   0,
        expenses:   realMpData.revenue - realMpData.profit,      expensesD: 0,
        taxes:      0,                                           taxesD:    0,
        adBudget:   realMpData.ad_spend,                         adBudgetD: 0,
      }
    : KPI[mp]

  const expSegs  = EXPENSES[mp]
  const totalExp = expSegs.reduce((s, e) => s + e.value, 0)

  // Real period rows (monthly granularity only, 'all' tab)
  const realPeriodRows: PeriodRow[] = (importData?.has_data && mp === 'all' && pmode === 'months')
    ? importData.by_period.map(p => ({
        period:      p.period_label,
        revenue:     p.revenue,
        commissions: p.commission,
        logistics:   p.logistics,
        storage:     0,
        advertising: p.ad_spend,
        taxes:       0,
        netProfit:   p.profit,
      }))
    : []

  const periodRows = realPeriodRows.length > 0 ? realPeriodRows : PERIOD[mp][pmode]

  const products = useMemo(() => {
    let base: ProductRow[]
    if (importData?.has_data && importData.by_product.length > 0) {
      const allReal: ProductRow[] = importData.by_product.map(p => ({
        name:     p.title || p.sku,
        mp:       [p.marketplace as MP],
        revenue:  p.revenue,
        expenses: p.revenue - p.profit,
        profit:   p.profit,
        margin:   p.margin,
        rating:   0,
        sales:    p.sales,
      }))
      base = mp === 'all' ? allReal : allReal.filter(p => p.mp.includes(mp))
    } else {
      base = mp === 'all' ? PRODUCTS : PRODUCTS.filter(p => p.mp.includes(mp))
    }
    return [...base].sort((a, b) => {
      const av = a[sortKey]; const bv = b[sortKey]
      if (typeof av === 'string') return sortDir === 'asc' ? (av as string).localeCompare(bv as string, 'ru') : (bv as string).localeCompare(av as string, 'ru')
      return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })
  }, [mp, sortKey, sortDir, importData])

  function handleSort(key: ProductSortKey) {
    if (key === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  function exportCSV() {
    const S = ';'
    const rows: (string|number)[][] = [
      [`Финансовый пакет — ${TABS.find(t => t.id === mp)?.label} — ${new Date().toLocaleDateString('ru-RU')}`],
      [],
      ['KPI'],
      ['Выручка (gross)', kpi.revenue],
      ['Чистая прибыль',  kpi.profit ],
      ['Маржинальность %',kpi.margin ],
      ['Расходы всего',   kpi.expenses],
      ['Налоги к уплате', kpi.taxes  ],
      ['Рекламный бюджет',kpi.adBudget],
      [],
      ['Структура расходов'],
      ['Статья','Сумма','Доля %'],
      ...expSegs.map(e => [e.label, e.value, ((e.value/totalExp)*100).toFixed(1)]),
      [],
      [`Движение по периодам (${pmode === 'months' ? 'месяцы' : pmode === 'weeks' ? 'недели' : 'дни'})`],
      PERIOD_HEADERS,
      ...periodRows.map(r => {
        const m = r.revenue > 0 ? ((r.netProfit/r.revenue)*100).toFixed(1) : '0.0'
        return [r.period, r.revenue, r.commissions, r.logistics, r.storage, r.advertising, r.taxes, r.netProfit, `${m}%`]
      }),
      [],
      ['Движение по товарам'],
      ['Товар','Площадки','Выручка','Расходы','Прибыль','Маржа %','Рейтинг','Продажи'],
      ...products.map(p => [p.name, p.mp.join(', '), p.revenue, p.expenses, p.profit, `${p.margin.toFixed(1)}%`, p.rating, p.sales]),
    ]
    const csv = '﻿' + rows.map(r => r.join(S)).join('\r\n')
    const blob = new Blob([csv], { type:'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `finance_${mp}_${new Date().toISOString().slice(0,10)}.csv`
    a.click(); URL.revokeObjectURL(url)
  }

  // KPI card definitions
  const kpiCards = [
    { label:'Выручка (gross)',   value:`${fmtM(kpi.revenue)}`,          delta:kpi.revenueD,   invert:false },
    { label:'Чистая прибыль',   value:`${fmtM(kpi.profit)}`,            delta:kpi.profitD,    invert:false },
    { label:'Маржинальность',   value:`${kpi.margin.toFixed(1)} %`,     delta:kpi.marginD,    invert:false },
    { label:'Расходы всего',    value:`${fmtM(kpi.expenses)}`,          delta:kpi.expensesD,  invert:true  },
    { label:'Налоги к уплате',  value:`${fmtM(kpi.taxes)}`,             delta:kpi.taxesD,     invert:true  },
    { label:'Рекламный бюджет', value:`${fmtM(kpi.adBudget)}`,          delta:kpi.adBudgetD,  invert:true  },
  ]

  return (
    <>
      <div className="flex-1 p-4 md:p-6" style={{ background: '#0B0B0F' }}>
        <div className="max-w-[1280px] mx-auto space-y-6">

          {/* Header */}
          <BlurFade inView>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold" style={{ color: '#F0EFEA', letterSpacing: '-0.02em' }}>Финансы</h1>
                <p className="text-sm text-muted-foreground mt-1">
                  {importData?.has_data
                    ? `Реальные данные · ${importData.row_count.toLocaleString('ru-RU')} строк${importData.last_import_date ? ` · обновлено ${importData.last_import_date}` : ''}`
                    : 'Тестовые данные · Май 2025'}
                </p>
              </div>
              <Button onClick={exportCSV} className="gap-2 shrink-0">
                <Download size={15} /> Сформировать пакет (CSV)
              </Button>
            </div>
          </BlurFade>

          {/* Tabs */}
          <BlurFade inView delay={0.04}>
            <div className="flex gap-1 p-1 rounded-xl bg-muted/60 w-fit overflow-x-auto">
              {TABS.map(t => (
                <button
                  key={t.id}
                  onClick={() => setMp(t.id)}
                  className="px-4 py-2 rounded-lg text-sm whitespace-nowrap transition-all"
                  style={{
                    fontWeight: mp === t.id ? 600 : 400,
                    background: mp === t.id ? 'rgba(124,58,237,0.12)' : 'transparent',
                    color: mp === t.id ? '#A78BFA' : '#7A7976',
                    boxShadow: mp === t.id ? '0 1px 4px rgba(0,0,0,0.3)' : 'none',
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </BlurFade>

          {/* 6 KPI Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
            {kpiCards.map((k, i) => {
              const up     = k.delta > 0
              const isGood = k.invert ? !up : up
              const dColor = isGood ? '#A78BFA' : '#EF4444'
              return (
                <BlurFade key={i} inView delay={i * 0.05}>
                  <Card className="shadow-stripe">
                    <CardContent className="p-4">
                      <p className="text-xs text-muted-foreground mb-2 leading-tight">{k.label}</p>
                      <p className="font-bold text-sm leading-snug mb-1.5" style={{ color: '#F0EFEA' }}>{k.value}</p>
                      <div className="flex items-center gap-1" style={{ fontSize: '0.7rem', color: dColor, fontWeight: 600 }}>
                        {up ? <TrendingUp size={11}/> : <TrendingDown size={11}/>}
                        {up ? '+' : ''}{k.delta.toFixed(1)}% vs пред.
                      </div>
                    </CardContent>
                  </Card>
                </BlurFade>
              )
            })}
          </div>

          {/* Expense Breakdown */}
          <BlurFade inView delay={0.06}>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card className="shadow-stripe">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base" style={{ color: '#F0EFEA' }}>Структура расходов</CardTitle>
                </CardHeader>
                <CardContent><FinanceChart segments={expSegs} size={180}/></CardContent>
              </Card>
              <Card className="shadow-stripe">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base" style={{ color: '#F0EFEA' }}>Детализация расходов</CardTitle>
                </CardHeader>
                <CardContent>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-border">
                        {['Статья','Сумма','Доля'].map((h, i) => (
                          <th key={h} className={`py-2 text-xs text-muted-foreground font-medium ${i === 0 ? 'text-left' : 'text-right'}`}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {expSegs.map((e, i) => (
                        <tr key={i} className={i < expSegs.length-1 ? 'border-b border-border/40' : ''}>
                          <td className="py-2.5 flex items-center gap-2 text-sm" style={{ color: '#F0EFEA' }}>
                            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: e.color }}/>
                            {e.label}
                          </td>
                          <td className="py-2.5 text-right text-sm text-muted-foreground tabular-nums">{fmtM(e.value)}</td>
                          <td className="py-2.5 text-right text-sm font-semibold tabular-nums" style={{ color: '#A78BFA' }}>
                            {((e.value/totalExp)*100).toFixed(1)}%
                          </td>
                        </tr>
                      ))}
                      <tr className="border-t border-border">
                        <td className="pt-3 text-xs text-muted-foreground font-medium">Итого расходов</td>
                        <td className="pt-3 text-right text-sm font-bold tabular-nums" style={{ color: '#F0EFEA' }}>{fmtM(totalExp)}</td>
                        <td className="pt-3 text-right text-sm font-bold" style={{ color: '#F0EFEA' }}>100%</td>
                      </tr>
                    </tbody>
                  </table>
                </CardContent>
              </Card>
            </div>
          </BlurFade>

          {/* Period Movement */}
          <BlurFade inView delay={0.08}>
            <Card className="shadow-stripe overflow-hidden">
              <CardHeader className="pb-3 border-b border-border/60">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <CardTitle className="text-base" style={{ color: '#F0EFEA' }}>Движение денег по периодам</CardTitle>
                  <div className="flex gap-1 p-1 rounded-lg bg-muted/60 w-fit">
                    {(['months','weeks','days'] as PeriodMode[]).map(m => (
                      <button key={m} onClick={() => setPmode(m)}
                        className="px-3 py-1 rounded-md text-xs transition-all"
                        style={{
                          fontWeight: pmode === m ? 600 : 400,
                          background: pmode === m ? 'rgba(124,58,237,0.12)' : 'transparent',
                          color: pmode === m ? '#A78BFA' : '#7A7976',
                          boxShadow: pmode === m ? '0 1px 3px rgba(0,0,0,0.3)' : 'none',
                        }}>
                        {m === 'months' ? 'Месяцы' : m === 'weeks' ? 'Недели' : 'Дни'}
                      </button>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <div className="overflow-x-auto">
                <table className="w-full" style={{ minWidth: 920 }}>
                  <thead>
                    <tr className="border-b border-border/60 bg-muted/30">
                      {PERIOD_HEADERS.map((h, i) => (
                        <th key={h} className={`px-4 py-3 text-xs text-muted-foreground font-medium whitespace-nowrap ${i === 0 ? 'text-left' : 'text-right'}`}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {periodRows.map((r, i) => {
                      const margin = r.revenue > 0 ? (r.netProfit/r.revenue)*100 : 0
                      const loss   = r.netProfit < 0
                      return (
                        <tr key={i} className={`${i < periodRows.length-1 ? 'border-b border-border/40' : ''} hover:bg-muted/30 transition-colors`}>
                          <td className="px-4 py-3 text-sm font-medium whitespace-nowrap" style={{ color: '#F0EFEA' }}>{r.period}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums font-medium" style={{ color: '#F0EFEA' }}>{fmtM(r.revenue)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{fmtM(r.commissions)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{fmtM(r.logistics)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{fmtM(r.storage)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{fmtM(r.advertising)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{fmtM(r.taxes)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums font-semibold" style={{ color: loss ? '#EF4444' : '#A78BFA' }}>
                            {r.netProfit >= 0 ? '+' : '−'}{fmtM(r.netProfit)}
                          </td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums font-bold" style={{ color: margin < 0 ? '#EF4444' : '#A78BFA' }}>
                            {margin < 0 ? '−' : ''}{Math.abs(margin).toFixed(1)}%
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </BlurFade>

          {/* Product Movement */}
          <BlurFade inView delay={0.1}>
            <Card className="shadow-stripe overflow-hidden">
              <CardHeader className="pb-3 border-b border-border/60">
                <CardTitle className="text-base" style={{ color: '#F0EFEA' }}>Движение по товарам</CardTitle>
                <p className="text-xs text-muted-foreground mt-1">Убыточные выделены красным. Клик на заголовок — сортировка.</p>
              </CardHeader>
              <div className="overflow-x-auto">
                <table className="w-full" style={{ minWidth: 780 }}>
                  <thead>
                    <tr className="border-b border-border/60 bg-muted/30">
                      {PROD_COLS.map(col => (
                        <th
                          key={col.key}
                          className={`px-4 py-3 text-xs text-muted-foreground font-medium whitespace-nowrap cursor-pointer select-none hover:text-primary transition-colors ${col.right ? 'text-right' : 'text-left'}`}
                          onClick={() => handleSort(col.key)}
                        >
                          {col.label}{sortKey === col.key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {products.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="py-12 text-center">
                          <Package size={28} className="mx-auto mb-2 text-muted-foreground/40" />
                          <p className="text-sm text-muted-foreground">Нет товаров для выбранной площадки.</p>
                        </td>
                      </tr>
                    ) : products.map((p, i) => {
                      const loss = p.profit < 0
                      return (
                        <tr key={i} className={`${i < products.length-1 ? 'border-b border-border/40' : ''} hover:bg-muted/30 transition-colors`}
                          style={{ background: loss ? 'rgba(220,38,38,0.02)' : 'transparent' }}>
                          <td className="px-4 py-3">
                            <span className="flex items-center gap-2">
                              {loss
                                ? <AlertTriangle size={13} style={{ color: '#dc2626', flexShrink: 0 }}/>
                                : <Package size={13} style={{ color: 'rgba(124,58,237,0.5)', flexShrink: 0 }}/>
                              }
                              <span className="text-sm font-medium" style={{ color: loss ? '#EF4444' : '#F0EFEA' }}>{p.name}</span>
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums" style={{ color: loss ? '#EF4444' : '#F0EFEA' }}>{fmtM(p.revenue)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{fmtM(p.expenses)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums font-semibold" style={{ color: loss ? '#EF4444' : '#A78BFA' }}>
                            {p.profit >= 0 ? '+' : '−'}{fmtM(p.profit)}
                          </td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums font-bold" style={{ color: loss ? '#EF4444' : '#A78BFA' }}>
                            {p.margin < 0 ? '−' : ''}{Math.abs(p.margin).toFixed(1)}%
                          </td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">★ {p.rating.toFixed(1)}</td>
                          <td className="px-4 py-3 text-right text-sm tabular-nums text-muted-foreground">{p.sales.toLocaleString('ru-RU')} шт.</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </BlurFade>

          <p className="text-center text-xs text-muted-foreground">
            {importData?.has_data
              ? 'Реальные данные из импортированных CSV. Структура расходов — демо.'
              : 'Демо-данные. Данные по площадкам рассчитаны пропорционально от общей суммы.'}
          </p>

          {/* Share success banner */}
          <BlurFade inView delay={0.12}>
            <Card className="shadow-stripe" style={{ borderColor: 'rgba(124,58,237,0.2)', background: 'rgba(124,58,237,0.04)' }}>
              <CardContent className="p-5">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div>
                    <p className="font-semibold text-sm flex items-center gap-2" style={{ color: '#F0EFEA' }}>
                      <Trophy size={14} style={{ color: '#A78BFA' }} /> Поделиться успехом
                    </p>
                    <p className="text-xs mt-1 text-muted-foreground">Расскажите о результатах — история появится в Обзоре рынка</p>
                  </div>
                  <Button variant="outline" onClick={() => setShowShare(true)} className="shrink-0 gap-2" style={{ borderColor: 'rgba(124,58,237,0.3)', color: '#A78BFA' }}>
                    <Trophy size={13} /> Поделиться успехом
                  </Button>
                </div>
              </CardContent>
            </Card>
          </BlurFade>

        </div>
      </div>

      {showShare && (() => {
        const autoTitle = kpi.profit > 0
          ? `Вышел на ${Math.round(kpi.profit / 1000)}K ₽/мес чистой прибыли`
          : `Выручка ${Math.round(kpi.revenue / 1000)}K ₽`
        return <ShareSuccessModal autoTitle={autoTitle} onClose={() => setShowShare(false)} />
      })()}
    </>
  )
}

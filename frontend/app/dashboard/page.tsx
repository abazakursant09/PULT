'use client'
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import { getProducts, marketplaceSummary, daysLeft, mono, rub, MP_NAME, lensDetail, type SellerProduct } from '@/lib/pultSeller'
import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'

const ACT: Record<string, string> = { 'Реклама': 'Снизить ставку', 'Документы': 'Загрузить', 'Отзывы': 'Ответить', 'Возвраты': 'Разобрать', 'SEO': 'Обновить SEO', 'Цена': 'Поднять цену' }
const sevRank = { loss: 0, warn: 1, gain: 2 } as const

export default function Home() {
  const router = useRouter()
  useEffect(() => { if (!localStorage.getItem('token')) router.push('/login') }, [router])

  const products = getProducts()
  const mps = marketplaceSummary()
  const totalLoss = products.filter(p => p.pr < 0).reduce((a, p) => a + p.pr, 0)

  // пожары: проблемные линзы + критичные остатки, отранжированы
  type Fire = { p: SellerProduct; ico: 'loss' | 'warn'; ms: string; amt: string; amtCls: string; act: string; rank: number }
  const fires: Fire[] = []
  for (const p of products) {
    for (const l of p.L) {
      if (l.sev === 'gain') continue
      const d = lensDetail(l.label)
      fires.push({ p, ico: l.sev, ms: d ? d.p : l.label, amt: l.label === 'Реклама' ? rub(p.pr) : l.sev === 'loss' ? 'риск' : '−топ', amtCls: l.sev === 'loss' ? 'neg' : 'amb', act: ACT[l.label] ?? 'Открыть', rank: sevRank[l.sev] })
    }
    const dl = daysLeft(p)
    if (dl <= 4) fires.push({ p, ico: 'warn', ms: `Заканчивается остаток — ${dl} дня до нуля`, amt: `${dl} дня`, amtCls: 'amb', act: 'Заказать', rank: 0.5 })
  }
  fires.sort((a, b) => a.rank - b.rank)
  const topFires = fires.slice(0, 5)

  const lowStock = products.slice().sort((a, b) => daysLeft(a) - daysLeft(b)).slice(0, 4)
  const opps = products.flatMap(p => p.L.filter(l => l.sev === 'gain').map(l => ({ p, l }))).slice(0, 3)
  const risks = products.flatMap(p => p.L.filter(l => l.label === 'Документы' || l.sev === 'loss').map(l => ({ p, l }))).filter(x => x.l.label === 'Документы').slice(0, 2)

  return (
    <>
      <SellerBar title="Главная" sub="Среда, 4 июня · доброе утро" />
      <div className="s-canvas">
        <DecisionFeedPanel />
        <div className="s-grid s-g4">
          <Link className="s-card s-clk" href="/dashboard/zakazy"><div className="s-k">Прибыль сегодня</div><div className="s-kpi pos num">+9 840 ₽<small className="s-muted">из 148 заказов</small></div></Link>
          <Link className="s-card s-clk" href="/dashboard/finance"><div className="s-k">Прибыль · 30 дней</div><div className="s-kpi num">248 600 ₽<small className="pos">▲ 6,2%</small></div></Link>
          <Link className="s-card s-clk" href="/dashboard/finance"><div className="s-k">Сейчас теряете</div><div className="s-kpi neg num">{rub(totalLoss)}<small className="s-muted">/мес</small></div></Link>
          <Link className="s-card s-clk" href="/dashboard/opportunities"><div className="s-k">Можно заработать</div><div className="s-kpi pos num">+58 700 ₽<small className="s-muted">/мес</small></div></Link>
        </div>

        <div className="s-sec"><h2>По маркетплейсам</h2><Link className="" href="/dashboard/products" style={{ fontSize: 12.5, color: 'var(--ac-2)' }}>Все товары →</Link></div>
        <div className="s-mprow">
          {mps.map(s => (
            <Link className="s-mpc s-clk" href={`/dashboard/products?mp=${s.m}`} key={s.m}><span className={`bd ${s.m}`} /><div><div className="nm">{MP_NAME[s.m]}</div><div className="ms">{s.count} товаров · {s.orders} заказов</div></div><span className={`v num ${s.profit >= 0 ? 'pos' : 'neg'}`}>{rub(s.profit)}</span></Link>
          ))}
        </div>

        <div className="s-sec"><h2>Что горит сейчас <span className="badge">{topFires.length}</span></h2><Link href="/dashboard/products" style={{ fontSize: 12.5, color: 'var(--ac-2)' }}>Все задачи →</Link></div>
        <div className="s-grid s-gfire">
          <div className="s-card">
            {topFires.map((f, i) => (
              <div className="s-fire" key={i}>
                <span className={`ico ${f.ico}`}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2 2 7l10 5 10-5z" /><path d="M2 7v10l10 5 10-5V7" /></svg></span>
                <div className="ti"><div className="nm">{f.p.n} · {MP_NAME[f.p.m]}</div><div className="ms">{f.ms}</div></div>
                <div className={`amt num ${f.amtCls}`}>{f.amt}</div>
                <Link href={`/dashboard/products/${f.p.id}`} className="s-firebtn pri">{f.act}</Link>
              </div>
            ))}
          </div>
          <div className="s-card">
            <div className="s-ch" style={{ color: 'var(--warn)' }}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 8 12 3 3 8l9 5z" /><path d="M3 8v8l9 5 9-5V8" /></svg>Надо заказать <Link href="/dashboard/sklad">Склад →</Link></div>
            {lowStock.map(p => {
              const d = daysLeft(p); const w = Math.min(60, d * 4); const c = d <= 4 ? 'var(--loss)' : d <= 8 ? 'var(--warn)' : 'var(--gain)'
              return (
                <Link className="s-stk s-clk" href="/dashboard/sklad" key={p.id}><span className="s-mono">{mono(p.n)}</span><div style={{ flex: 1 }}><div className="nm">{p.n}</div><div className="bar2"><i style={{ width: `${w}%`, background: c }} /></div></div><div className="days" style={{ color: c }}>{d} дн</div></Link>
              )
            })}
          </div>
        </div>

        <div className="s-grid s-g2e" style={{ marginTop: 16 }}>
          <div className="s-card">
            <div className="s-ch g"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 18 10 11l4 4 6-7" /><path d="M15 6h5v5" /></svg>Возможности · +58 700 ₽ <Link href="/dashboard/opportunities">Все →</Link></div>
            {opps.map(({ p, l }, i) => (
              <Link className="s-mini s-clk" href={`/dashboard/products/${p.id}`} key={i}><span className="s-mono">{mono(p.n)}</span><div><div className="nm">{p.n} · {MP_NAME[p.m]}</div><div className="ms">{lensDetail(l.label)?.s ?? l.label}</div></div><span className="amt pos num">{rub(Math.max(p.pr, 12800))}</span></Link>
            ))}
          </div>
          <div className="s-card">
            <div className="s-ch r"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3 4 6v6c0 5 3.5 7.5 8 9 4.5-1.5 8-4 8-9V6z" /></svg>Риски · {risks.length} активных <Link href="/dashboard/risks">Все →</Link></div>
            {risks.map(({ p }, i) => (
              <Link className="s-mini s-clk" href={`/dashboard/products/${p.id}`} key={i}><span className="s-mono">{mono(p.n)}</span><div><div className="nm">{p.n} · {MP_NAME[p.m]}</div><div className="ms">нет сертификата ЕАС</div></div><span className="amt neg">блокировка</span></Link>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}

'use client'
/**
 * ДЕНЬГИ → «Финансы». P&L под товар-центричную операционку.
 * Чистая прибыль/мес и потери считаются из pultSeller (реальная сумма по товарам),
 * расходы — расшифровка съедающих прибыль статей. Клик по товару → карточка.
 */
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import { getProducts, marketplaceSummary, mono, rub, MP_NAME } from '@/lib/pultSeller'

// расходные статьи (что съедает прибыль) — расшифровка, доля от расходов
const COSTS = [
  { k: 'Реклама', v: 312000, hint: 'CPM растёт, ДРР выше нормы по 3 товарам', to: '/dashboard/products' },
  { k: 'Комиссия МП', v: 286000, hint: 'WB 19% · Ozon 17% · Я.Маркет 15%', to: '/dashboard/products' },
  { k: 'Логистика и хранение', v: 248000, hint: 'Заморожено в остатках 1,24 млн ₽', to: '/dashboard/sklad' },
  { k: 'Возвраты и брак', v: 146000, hint: 'Возвраты выше нормы по 2 товарам', to: '/dashboard/products' },
]

export default function Finance() {
  const products = getProducts()
  const mps = marketplaceSummary()
  const net = products.reduce((a, p) => a + p.pr, 0)            // чистая прибыль/мес (сумма по товарам)
  const loss = products.filter(p => p.pr < 0).reduce((a, p) => a + p.pr, 0)
  const eaters = products.filter(p => p.pr < 0).slice().sort((a, b) => a.pr - b.pr)

  const costTotal = COSTS.reduce((a, c) => a + c.v, 0)
  const revenue = 1240600                                        // оценка выручки 30д
  const margin = Math.round((net / revenue) * 100)               // net и revenue — оба за мес

  return (
    <>
      <SellerBar title="Финансы" sub="Прибыль, расходы и что съедает деньги" />
      <div className="s-canvas">
        <div className="s-grid s-g4">
          <div className="s-card"><div className="s-k">Выручка · 30 дней</div><div className="s-kpi num">{revenue.toLocaleString('ru-RU')} ₽<small className="pos">▲ 6,2%</small></div></div>
          <div className="s-card"><div className="s-k">Чистая прибыль · мес</div><div className="s-kpi pos num">{rub(net)}</div></div>
          <div className="s-card"><div className="s-k">Сейчас теряете · мес</div><div className="s-kpi neg num">{rub(loss)}</div></div>
          <div className="s-card"><div className="s-k">Маржа</div><div className="s-kpi num">{margin}%<small className="s-muted">от выручки</small></div></div>
        </div>

        <div className="s-sec"><h2>Куда уходят деньги</h2><span className="s-muted" style={{ fontSize: 12.5 }}>{costTotal.toLocaleString('ru-RU')} ₽ / 30 дней</span></div>
        <div className="s-card">
          {COSTS.map((c, i) => {
            const share = Math.round((c.v / costTotal) * 100)
            return (
              <div className="s-fire" key={i}>
                <span className="ico loss"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v20" /><path d="M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></svg></span>
                <div className="ti">
                  <div className="nm">{c.k} <span className="s-muted" style={{ fontWeight: 500 }}>· {share}%</span></div>
                  <div className="ms">{c.hint}</div>
                  <div className="bar2" style={{ width: '100%', height: 4, borderRadius: 3, background: 'var(--panel-3)', marginTop: 7, overflow: 'hidden' }}><i style={{ display: 'block', height: '100%', width: `${share}%`, background: 'var(--loss)', borderRadius: 3 }} /></div>
                </div>
                <div className="amt num neg">−{c.v.toLocaleString('ru-RU')} ₽</div>
                <Link href={c.to} className="s-firebtn gho">Разобрать</Link>
              </div>
            )
          })}
        </div>

        <div className="s-sec"><h2>Прибыль по маркетплейсам</h2><Link href="/dashboard/products" style={{ fontSize: 12.5, color: 'var(--ac-2)' }}>Все товары →</Link></div>
        <div className="s-mprow">
          {mps.map(s => (
            <div className="s-mpc" key={s.m}><span className={`bd ${s.m}`} /><div><div className="nm">{MP_NAME[s.m]}</div><div className="ms">{s.count} товаров · {s.orders} заказов</div></div><span className={`v num ${s.profit >= 0 ? 'pos' : 'neg'}`}>{rub(s.profit)}</span></div>
          ))}
        </div>

        <div className="s-sec"><h2>Товары в минусе <span className="badge">{eaters.length}</span></h2></div>
        <div className="s-card" style={{ padding: '4px 4px' }}>
          <table className="s-tbl">
            <thead><tr><th>Товар</th><th>Маркетплейс</th><th>Прибыль/мес</th><th>Заказы/30д</th><th /></tr></thead>
            <tbody>
              {eaters.map(p => (
                <tr key={p.id}>
                  <td><div className="pn"><span className="s-mono">{mono(p.n)}</span><div><div style={{ fontWeight: 550 }}>{p.n}</div><div className="s-muted" style={{ fontSize: 11.5 }}>#{p.pos}</div></div></div></td>
                  <td><span className="s-bcol"><span className="b" style={{ background: `var(--${p.m})` }} />{MP_NAME[p.m]}</span></td>
                  <td className="num neg" style={{ fontWeight: 600 }}>{rub(p.pr)}</td>
                  <td className="num">{p.o30}</td>
                  <td style={{ textAlign: 'right' }}><Link href={`/dashboard/products/${p.id}`} className="s-tbtn pri">Исправить</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

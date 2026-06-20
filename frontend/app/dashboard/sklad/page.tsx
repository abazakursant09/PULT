'use client'
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import { getProducts, daysLeft, mono, MP_NAME } from '@/lib/pultSeller'

export default function Sklad() {
  const rows = getProducts().slice().sort((a, b) => daysLeft(a) - daysLeft(b))
  return (
    <>
      <SellerBar title="Склад и поставки" sub="Остатки и поставки" />
      <div className="s-canvas">
        <div className="s-grid s-g4" style={{ marginBottom: 8 }}>
          <div className="s-card"><div className="s-k">Заканчивается</div><div className="s-kpi sm neg num">4 товара</div></div>
          <div className="s-card"><div className="s-k">Заказать на сумму</div><div className="s-kpi sm num">186 000 ₽</div></div>
          <div className="s-card"><div className="s-k">В пути / поставки</div><div className="s-kpi sm num">2</div></div>
          <div className="s-card"><div className="s-k">Заморожено в остатках</div><div className="s-kpi sm num">1 240 000 ₽</div></div>
        </div>
        <div className="s-sec"><h2>Остатки по товарам</h2><Link href="/dashboard/products" style={{ fontSize: 12.5, color: 'var(--ac-2)' }}>Все товары →</Link></div>
        <div className="s-card" style={{ padding: '4px 4px' }}>
          <table className="s-tbl">
            <thead><tr><th>Товар</th><th>Маркетплейс</th><th>Остаток</th><th>Скорость</th><th>Хватит на</th><th>Статус</th><th /></tr></thead>
            <tbody>
              {rows.map(p => {
                const d = daysLeft(p); const st = d <= 4 ? ['red', 'срочно'] : d <= 8 ? ['amb', 'скоро'] : ['ok', 'ок']
                return (
                  <tr key={p.id}>
                    <td><div className="pn"><span className="s-mono">{mono(p.n)}</span><div><div style={{ fontWeight: 550 }}>{p.n}</div><div className="s-muted" style={{ fontSize: 11.5 }}>#{p.pos}</div></div></div></td>
                    <td><span className="s-bcol"><span className="b" style={{ background: `var(--${p.m})` }} />{MP_NAME[p.m]}</span></td>
                    <td className="num">{p.stock} шт</td>
                    <td className="num">{p.spd}/дн</td>
                    <td className={`num ${st[0] === 'red' ? 'neg' : st[0] === 'amb' ? 'amb' : ''}`} style={{ fontWeight: 600 }}>{d} дн</td>
                    <td><span className={`s-pill ${st[0]}`}>{st[1]}</span></td>
                    <td style={{ textAlign: 'right' }}><Link href={`/dashboard/products/${p.id}`} className={`s-tbtn ${d <= 8 ? 'pri' : 'gho'}`}>{d <= 8 ? 'Заказать' : 'Поставка'}</Link></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

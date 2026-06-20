'use client'
import { SellerBar } from '@/components/seller/Shell'
import { getOrders, marketplaceSummary, MP_NAME } from '@/lib/pultSeller'

const STP: Record<string, string> = { 'выкуплен': 'ok', 'в пути': 'amb', 'возврат': 'red' }

export default function Zakazy() {
  const orders = getOrders()
  const mps = marketplaceSummary()
  const wbBuy = [88, 84, 79]
  const mpSums = [176200, 112800, 23400]
  return (
    <>
      <SellerBar title="Заказы" sub="Продажи, выкуп, возвраты" />
      <div className="s-canvas">
        <div className="s-grid s-g5">
          <div className="s-card"><div className="s-k">Заказов сегодня</div><div className="s-kpi sm num">148</div></div>
          <div className="s-card"><div className="s-k">Сумма</div><div className="s-kpi sm num">312 400 ₽</div></div>
          <div className="s-card"><div className="s-k">Выкуп</div><div className="s-kpi sm pos num">86%</div></div>
          <div className="s-card"><div className="s-k">Возвраты</div><div className="s-kpi sm amb num">7%</div></div>
          <div className="s-card"><div className="s-k">Ср. чек</div><div className="s-kpi sm num">2 110 ₽</div></div>
        </div>

        <div className="s-sec"><h2>Заказы по маркетплейсам · сегодня</h2></div>
        <div className="s-mprow">
          {mps.map((s, i) => (
            <div className="s-mpc" key={s.m}><span className={`bd ${s.m}`} /><div><div className="nm">{MP_NAME[s.m]}</div><div className="ms">{s.orders} заказов · выкуп {wbBuy[i]}%</div></div><span className="v num">{mpSums[i].toLocaleString('ru-RU')} ₽</span></div>
          ))}
        </div>

        <div className="s-sec"><h2>Последние заказы</h2><span className="s-muted" style={{ fontSize: 12.5 }}>показаны последние {orders.length}</span></div>
        <div className="s-card" style={{ padding: '4px 4px' }}>
          <table className="s-tbl">
            <thead><tr><th>Время</th><th>Товар</th><th>Маркетплейс</th><th>Сумма</th><th>Статус</th></tr></thead>
            <tbody>
              {orders.map((o, i) => (
                <tr key={i}>
                  <td className="s-muted num">{o.time}</td>
                  <td style={{ fontWeight: 550 }}>{o.product}</td>
                  <td><span className="s-bcol"><span className="b" style={{ background: `var(--${o.m})` }} />{MP_NAME[o.m]}</span></td>
                  <td className="num">{o.sum.toLocaleString('ru-RU')} ₽</td>
                  <td><span className={`s-pill ${STP[o.status]}`}>{o.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

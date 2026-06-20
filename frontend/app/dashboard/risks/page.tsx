'use client'
/**
 * ЗАЩИТА → «Риски». Угрозы бизнесу: документы/блокировки · бренд · жалобы · возвраты.
 * Линзы-риски по товарам из pultSeller. Цель: какой ТОВАР под угрозой и что сделать.
 */
import { useState } from 'react'
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import { getProducts, lensDetail, mono, MP_NAME, type SellerProduct, type Lens } from '@/lib/pultSeller'

const RISK_LABELS = ['Документы', 'Отзывы', 'Возвраты']
type Risk = { p: SellerProduct; l: Lens }

export default function Risks() {
  const all: Risk[] = getProducts().flatMap(p =>
    p.L.filter(l => RISK_LABELS.includes(l.label)).map(l => ({ p, l }))
  ).sort((a, b) => (a.l.sev === 'loss' ? 0 : 1) - (b.l.sev === 'loss' ? 0 : 1))

  const cats = ['all', ...RISK_LABELS] as const
  const [cat, setCat] = useState<(typeof cats)[number]>('all')
  const cnt: Record<string, number> = { all: all.length }
  RISK_LABELS.forEach(k => { cnt[k] = all.filter(r => r.l.label === k).length })
  const list = cat === 'all' ? all : all.filter(r => r.l.label === cat)
  const critical = all.filter(r => r.l.sev === 'loss').length

  return (
    <>
      <SellerBar title="Риски" sub="Документы · Бренд · Жалобы · Возвраты — что под угрозой" />
      <div className="s-canvas">
        <div className="s-grid s-g3">
          <div className="s-card"><div className="s-k">Критичные риски</div><div className="s-kpi sm neg num">{critical}</div></div>
          <div className="s-card"><div className="s-k">Всего рисков</div><div className="s-kpi sm num">{all.length}</div></div>
          <div className="s-card"><div className="s-k">Товаров под угрозой</div><div className="s-kpi sm num">{new Set(all.map(r => r.p.id)).size}</div></div>
        </div>

        <div className="s-mptabs" style={{ marginTop: 18 }}>
          {cats.map(c => (
            <button key={c} className={`s-mptab${cat === c ? ' on' : ''}`} onClick={() => setCat(c)}>
              {c === 'all' ? 'Все' : c}<span className="c">{cnt[c]}</span>
            </button>
          ))}
        </div>

        {list.length ? (
          <div className="s-card" style={{ padding: '4px 4px', marginTop: 16 }}>
            <table className="s-tbl">
              <thead><tr><th>Товар</th><th>Маркетплейс</th><th>Риск</th><th>Последствие</th><th /></tr></thead>
              <tbody>
                {list.map((r, i) => {
                  const d = lensDetail(r.l.label)
                  const sev = r.l.sev === 'loss' ? 'red' : 'amb'
                  return (
                    <tr key={r.p.id + i}>
                      <td><div className="pn"><span className="s-mono">{mono(r.p.n)}</span><div><div style={{ fontWeight: 550 }}>{r.p.n}</div><div className="s-muted" style={{ fontSize: 11.5 }}>{d?.c ?? r.l.label}</div></div></div></td>
                      <td><span className="s-bcol"><span className="b" style={{ background: `var(--${r.p.m})` }} />{MP_NAME[r.p.m]}</span></td>
                      <td><span className={`s-pill ${sev}`}>{r.l.label}</span></td>
                      <td className="s-muted">{d?.p ?? '—'}</td>
                      <td style={{ textAlign: 'right' }}><Link href={`/dashboard/products/${r.p.id}`} className="s-tbtn pri">{d?.s ?? 'Разобрать'}</Link></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="s-card" style={{ textAlign: 'center', padding: 30, marginTop: 16 }}>
            <div style={{ fontSize: 22, marginBottom: 6 }}>🛡</div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>Высоких рисков нет</div>
            <div className="s-muted" style={{ fontSize: 13, marginTop: 4 }}>Документы, бренд и жалобы под контролем.</div>
          </div>
        )}

        <div className="s-card s-muted" style={{ textAlign: 'center', fontSize: 12.5, marginTop: 14, padding: 16 }}>
          ⬆ Журнал документов и сертификатов подгружается из CSV-импорта — подключите в Настройках
        </div>
      </div>
    </>
  )
}

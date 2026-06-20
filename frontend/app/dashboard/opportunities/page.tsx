'use client'
/**
 * РОСТ → «Продвижение». Только потенциал прибыли (+₽/мес): цена / SEO / реклама.
 * Линзы sev='gain' по товарам из pultSeller. Действие — реальный Проверить/Выполнить.
 */
import { useState } from 'react'
import Link from 'next/link'
import { SellerBar, SellerAction } from '@/components/seller/Shell'
import { getProducts, lensDetail, mono, rub, MP_NAME, type SellerProduct, type Lens } from '@/lib/pultSeller'

type Opp = { p: SellerProduct; l: Lens; gain: number }

// оценка эффекта в ₽/мес от возможности (mock, ingest-ready)
const GAIN: Record<string, number> = { 'Цена': 18600, 'SEO': 24800, 'Реклама': 31200, 'Отзывы': 9400 }

export default function Opportunities() {
  const [open, setOpen] = useState<string | null>(null)
  const opps: Opp[] = getProducts().flatMap(p =>
    p.L.filter(l => l.sev === 'gain').map(l => ({ p, l, gain: GAIN[l.label] ?? 12000 }))
  ).sort((a, b) => b.gain - a.gain)

  const total = opps.reduce((a, o) => a + o.gain, 0)

  return (
    <>
      <SellerBar title="Продвижение" sub="Что сделать, чтобы заработать больше" />
      <div className="s-canvas">
        <div className="s-grid s-g3">
          <div className="s-card"><div className="s-k">Потенциал роста · мес</div><div className="s-kpi pos num">+{total.toLocaleString('ru-RU')} ₽</div></div>
          <div className="s-card"><div className="s-k">Возможностей</div><div className="s-kpi sm num">{opps.length}</div></div>
          <div className="s-card"><div className="s-k">Товаров с ростом</div><div className="s-kpi sm num">{new Set(opps.map(o => o.p.id)).size}</div></div>
        </div>

        <div className="s-sec"><h2>Возможности роста</h2><Link href="/dashboard/products" style={{ fontSize: 12.5, color: 'var(--ac-2)' }}>Все товары →</Link></div>
        {opps.map((o, i) => {
          const d = lensDetail(o.l.label)
          const id = o.p.id + i
          return (
            <div className="s-card" key={id} style={{ marginBottom: 12 }}>
              <div className="s-fire" style={{ borderTop: 0, paddingTop: 0 }}>
                <span className="ico" style={{ background: 'rgba(84,190,140,.1)', color: 'var(--gain)' }}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 18 10 11l4 4 6-7" /><path d="M15 6h5v5" /></svg></span>
                <div className="ti">
                  <div className="nm">{o.p.n} · {MP_NAME[o.p.m]}</div>
                  <div className="ms">{d ? `${d.p} → ${d.s}` : `${o.l.label}: возможность роста`}</div>
                </div>
                <div className="amt num pos">+{o.gain.toLocaleString('ru-RU')} ₽</div>
                <span className="s-chip"><span className="dot gain" />{o.l.label}</span>
              </div>
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--hair)' }}>
                {open === id
                  ? <SellerAction insightKey={o.l.insightKey} />
                  : <div className="s-rowact"><button className="s-btn gho" onClick={() => setOpen(id)}>Открыть действие</button><Link href={`/dashboard/products/${o.p.id}`} className="s-btn gho">Карточка товара</Link><span className="s-note">эффект {d?.e ?? `+${o.gain.toLocaleString('ru-RU')} ₽/мес`}</span></div>}
              </div>
            </div>
          )
        })}
        {!opps.length && <div className="s-card s-muted" style={{ textAlign: 'center', padding: 30 }}>Возможностей роста сейчас нет.</div>}
      </div>
    </>
  )
}

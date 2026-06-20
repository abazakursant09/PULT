'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import { getProducts, byMp, daysLeft, mono, rub, MP_NAME, type MP } from '@/lib/pultSeller'

export default function Products() {
  const [mp, setMp] = useState<MP | 'all'>('all')
  const [q, setQ] = useState('')
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search)
    const m = sp.get('mp'); if (m === 'wb' || m === 'ozon' || m === 'ym') setMp(m)
    const qq = sp.get('q'); if (qq) setQ(qq)
  }, [])
  const all = getProducts()
  const cnt = { all: all.length, wb: byMp('wb').length, ozon: byMp('ozon').length, ym: byMp('ym').length }
  const list = byMp(mp).filter(p => !q || p.n.toLowerCase().includes(q.toLowerCase()))
  const tabs: ({ k: MP | 'all'; l: string })[] = [{ k: 'all', l: 'Все' }, { k: 'wb', l: 'Wildberries' }, { k: 'ozon', l: 'Ozon' }, { k: 'ym', l: 'Я.Маркет' }]

  return (
    <>
      <SellerBar title="Товары" sub={q ? `Поиск: «${q}» — ${list.length} найдено` : '12 товаров по маркетплейсам'} />
      <div className="s-canvas">
        {q && <div className="s-mptabs" style={{ marginBottom: 10 }}><button className="s-mptab on" onClick={() => setQ('')}>Поиск: «{q}» ✕</button></div>}
        <div className="s-mptabs">
          {tabs.map(t => (
            <button key={t.k} className={`s-mptab${mp === t.k ? ' on' : ''}`} onClick={() => setMp(t.k)}>
              {t.k !== 'all' && <span className="b" style={{ background: `var(--${t.k})` }} />}{t.l}<span className="c">{cnt[t.k]}</span>
            </button>
          ))}
        </div>
        <div className="s-cards">
          {list.map(p => {
            const d = daysLeft(p); const dc = d <= 4 ? 'neg' : d <= 8 ? 'amb' : ''
            return (
              <Link key={p.id} href={`/dashboard/products/${p.id}`} className="s-pc">
                <div className="top"><div className="s-mono">{mono(p.n)}</div><div><div className="nm">{p.n}</div><div className="mp"><span className="b" style={{ background: `var(--${p.m})` }} />{MP_NAME[p.m]} · #{p.pos}</div></div></div>
                <div className="stats">
                  <div className="st"><div className="l">прибыль/мес</div><div className={`v num ${p.pr >= 0 ? 'pos' : 'neg'}`}>{rub(p.pr)}</div></div>
                  <div className="st"><div className="l">заказы/30д</div><div className="v num">{p.o30}</div></div>
                  <div className="st"><div className="l">остаток</div><div className={`v num ${dc}`}>{d} дн</div></div>
                </div>
                <div className="lens">{p.L.map((l, i) => <span className="s-chip" key={i}><span className={`dot ${l.sev}`} />{l.label}</span>)}</div>
                <div className="cta"><span>Открыть карточку</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m9 6 6 6-6 6" /></svg></div>
              </Link>
            )
          })}
        </div>
      </div>
    </>
  )
}

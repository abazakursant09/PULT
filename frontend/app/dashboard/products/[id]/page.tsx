'use client'
import { useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { SellerBar, SellerAction } from '@/components/seller/Shell'
import { byId, daysLeft, mono, rub, MP_NAME, lensDetail } from '@/lib/pultSeller'
import LearningSurface from '@/components/LearningSurface'
import SeoPanel from '@/components/seo/SeoPanel'
import AdvertisingPanel from '@/components/advertising/AdvertisingPanel'
import ReviewAssistantPanel from '@/components/review/ReviewAssistantPanel'

// PULT marketplace codes → backend canonical marketplace (agnostic SEO API).
const MP_CANON: Record<string, string> = { wb: 'wildberries', ozon: 'ozon', ym: 'yandex' }

export default function ProductCard() {
  const params = useParams()
  const p = byId(String(params?.id ?? ''))
  const [tab, setTab] = useState(0)

  if (!p) return (
    <>
      <SellerBar title="Карточка товара" />
      <div className="s-canvas"><Link href="/dashboard/products" className="s-back">← Все товары</Link><div className="s-card">Товар не найден.</div></div>
    </>
  )

  const d = daysLeft(p)
  const tabs = ['Прибыль', ...p.L.map(l => l.label)]

  return (
    <>
      <SellerBar title="Карточка товара" sub="Всё о товаре в одном месте" />
      <div className="s-canvas">
        <Link href="/dashboard/products" className="s-back">← Все товары</Link>
        <div className="s-phead">
          <div className="s-mono">{mono(p.n)}</div>
          <div>
            <h1>{p.n}</h1>
            <div className="sub"><span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 8, height: 8, borderRadius: 2, background: `var(--${p.m})` }} />{MP_NAME[p.m]}</span><span>позиция #{p.pos}</span><span>★ {p.rt}</span></div>
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}><div className="s-k" style={{ margin: 0 }}>прибыль / мес</div><div className={`s-kpi num ${p.pr >= 0 ? 'pos' : 'neg'}`}>{rub(p.pr)}</div></div>
        </div>

        <div className="s-grid s-g5">
          <div className="s-card"><div className="s-k">Выручка/мес</div><div className="s-kpi sm num">{(p.o30 * 2110).toLocaleString('ru-RU')} ₽</div></div>
          <div className="s-card"><div className="s-k">Заказы/30д</div><div className="s-kpi sm num">{p.o30}</div></div>
          <div className="s-card"><div className="s-k">Рейтинг</div><div className="s-kpi sm num">{p.rt}</div></div>
          <div className="s-card"><div className="s-k">Остаток</div><div className={`s-kpi sm num ${d <= 4 ? 'neg' : d <= 8 ? 'amb' : ''}`}>{d} дн</div></div>
          <div className="s-card"><div className="s-k">Скорость</div><div className="s-kpi sm num">{p.spd}/дн</div></div>
        </div>

        <div className="s-ltabs" role="tablist">
          {tabs.map((t, i) => <button key={t} role="tab" aria-selected={tab === i} className={`s-ltab${tab === i ? ' on' : ''}`} onClick={() => setTab(i)}>{t}</button>)}
        </div>

        {tab === 0 ? (
          <div className="s-card" style={{ marginBottom: 14 }}>
            <div className="s-frm">
              <div className="c"><div className="l">Выручка / мес</div><div className="v num">{(p.o30 * 2110).toLocaleString('ru-RU')} ₽</div></div>
              <div className="c"><div className="l">Заказы / 30 дней</div><div className="v num">{p.o30}</div></div>
              <div className="c"><div className="l">Прибыль / мес</div><div className="v num" style={{ color: p.pr >= 0 ? 'var(--gain)' : 'var(--loss)', fontWeight: 600 }}>{rub(p.pr)}</div></div>
              <div className="c"><div className="l">Скорость · остаток</div><div className="v num">{p.spd} шт/дн · {d} дн</div></div>
            </div>
            <div className="s-note" style={{ marginTop: 12 }}>Детали проблем и роста — на вкладках линз выше.</div>
          </div>
        ) : (() => {
          const l = p.L[tab - 1]; const x = lensDetail(l.label)
          if (!x) return <div className="s-card s-muted">Нет данных по линзе «{l.label}».</div>
          return (
            <div className="s-card" style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 14 }}><span className="s-chip"><span className={`dot ${l.sev}`} />{l.label}</span></div>
              <div className="s-frm">
                <div className="c"><div className="l">Проблема</div><div className="v">{x.p}</div></div>
                <div className="c"><div className="l">Причина</div><div className="v">{x.c}</div></div>
                <div className="c"><div className="l">Решение</div><div className="v">{x.s}</div></div>
                <div className="c g"><div className="l">Эффект</div><div className="v">{x.e}</div></div>
              </div>
              <SellerAction insightKey={l.insightKey} />
              {l.insightKey && (
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
                  <div className="s-k" style={{ marginBottom: 8 }}>Почему PULT рекомендует это действие</div>
                  <LearningSurface insightKey={l.insightKey} listingId={p.id} />
                </div>
              )}
            </div>
          )
        })()}

        {(() => {
          const mp = MP_CANON[p.m] ?? p.m
          // p.id — это id из каталога (пока не реальный backend ProductListing.id),
          // поэтому работаем в ручном режиме под synthetic namespace, чтобы не
          // создавать аудиты под мусорный listing_id. Реальный listing mode — позже.
          const seoListingId = `seo-manual:${mp}:${p.id}`
          return (
            <div className="s-card" style={{ marginBottom: 14 }}>
              <div className="s-k" style={{ marginBottom: 10 }}>SEO-аудит карточки</div>
              <SeoPanel listingId={seoListingId} marketplace={mp} manual />
            </div>
          )
        })()}

        {(() => {
          const mp = MP_CANON[p.m] ?? p.m
          // p.id — каталожный id (не реальный backend listing). Реклама берёт
          // цифры из импорта финансов по SKU; аудиты держим под synthetic namespace.
          const advListingId = `adv-demo:${mp}:${p.id}`
          return (
            <div className="s-card" style={{ marginBottom: 14 }}>
              <div className="s-k" style={{ marginBottom: 10 }}>Реклама: влияние на прибыль</div>
              <AdvertisingPanel listingId={advListingId} marketplace={mp} />
            </div>
          )
        })()}

        {(() => {
          const mp = MP_CANON[p.m] ?? p.m
          // Репутация: сигналы по отзывам не привязаны к listing_id на бэкенде,
          // поэтому панель работает в user-wide режиме; marketplace — контекст.
          return (
            <div className="s-card" style={{ marginBottom: 14 }}>
              <div className="s-k" style={{ marginBottom: 10 }}>Репутация: управление отзывами</div>
              <ReviewAssistantPanel marketplace={mp} />
            </div>
          )
        })()}
      </div>
    </>
  )
}

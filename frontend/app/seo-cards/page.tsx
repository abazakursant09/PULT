'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { AppShell } from '@/components/AppShell'

// ─── Types ────────────────────────────────────────────────────────────────────

type StyleKey = 'Продающий' | 'Экспертный' | 'Эмоциональный' | 'Минималист' | 'Сторителлинг'

interface Product {
  id: string
  name: string
  shortName: string
  price: string
  oldPrice?: string
  discount?: number
  benefits: [string, string, string]
  specs: [string, string, string]
  tag: string
  bg: string
  accent: string
  accentDark: string
  quote: string
  question: string
}

interface EditData {
  name: string
  price: string
  oldPrice: string
  benefit1: string
  benefit2: string
  benefit3: string
  specs0: string
  specs1: string
  specs2: string
  photo: string
}

// ─── Demo Products ────────────────────────────────────────────────────────────

const PRODUCTS: Product[] = [
  {
    id: 'cream',
    name: 'Крем для лица «Увлажняющий» 75 мл',
    shortName: 'Крем «Увлажняющий»',
    price: '890 ₽', oldPrice: '1 290 ₽', discount: 31,
    benefits: ['Гиалуроновая кислота', 'SPF 30', 'Для всех типов кожи'],
    specs: ['75 мл', 'Made in Korea', 'Hypoallergenic'],
    tag: 'Корейский уход',
    bg: 'linear-gradient(150deg,#FFB6C1 0%,#FF8FAB 30%,#E91E8C 65%,#880E4F 100%)',
    accent: '#FF4081', accentDark: '#C2185B',
    quote: 'Мы создали этот крем, потому что сами устали от тяжёлых формул. Лёгкий, но глубокий уход.',
    question: 'Что если крем впитывается за 30 секунд?',
  },
  {
    id: 'sneakers',
    name: 'Кроссовки «AirRun Pro»',
    shortName: 'AirRun Pro',
    price: '4 990 ₽', oldPrice: '6 990 ₽', discount: 29,
    benefits: ['Амортизация Gel', 'Дышащая сетка', 'Вес 280 г'],
    specs: ['39–45 размер', 'Текстиль/полимер', 'Чёрный'],
    tag: 'Sport Edition',
    bg: 'linear-gradient(150deg,#1a1a2e 0%,#16213e 35%,#0f3460 65%,#533483 100%)',
    accent: '#E94560', accentDark: '#B71C1C',
    quote: 'Каждый шаг — как на воздушной подушке. Создано для тех, кто не останавливается.',
    question: 'Что если кроссовки весят меньше 300 г?',
  },
  {
    id: 'case',
    name: 'Чехол для iPhone «ShockProof»',
    shortName: 'ShockProof',
    price: '590 ₽', oldPrice: undefined, discount: undefined,
    benefits: ['Защита от падений 2 м', 'MagSafe совместим', 'Тонкий 1.2 мм'],
    specs: ['iPhone 14/15', 'Силикон', '25 г'],
    tag: 'MagSafe',
    bg: 'linear-gradient(150deg,#0F2027 0%,#203A43 45%,#2C5364 75%,#1565C0 100%)',
    accent: '#00D4FF', accentDark: '#0288D1',
    quote: 'Создан инженерами, которые сами разбивали экраны. Больше ни одного разбитого дисплея.',
    question: 'Что если чехол тоньше 2 мм, но выдержит падение с 2 метров?',
  },
  {
    id: 'organizer',
    name: 'Органайзер для ванной «SpaceMax»',
    shortName: 'SpaceMax',
    price: '1 490 ₽', oldPrice: '2 290 ₽', discount: 35,
    benefits: ['6 отсеков', 'Влагостойкий', 'Крепление без сверления'],
    specs: ['30×20×10 см', 'ABS-пластик', 'Белый'],
    tag: 'No Drill',
    bg: 'linear-gradient(150deg,#004D40 0%,#00695C 35%,#00897B 65%,#26A69A 100%)',
    accent: '#64FFDA', accentDark: '#00897B',
    quote: 'Мы устали от беспорядка в ванной. SpaceMax — 6 минут монтажа и годы порядка.',
    question: 'А что если навести порядок в ванной за 6 минут?',
  },
  {
    id: 'bars',
    name: 'Батончики «FitBar» 12 шт',
    shortName: 'FitBar',
    price: '890 ₽', oldPrice: '1 290 ₽', discount: 31,
    benefits: ['20 г белка', 'Без сахара', '3 вкуса в наборе'],
    specs: ['12 шт × 40 г', '180 ккал', 'Шоколад/клубника/ваниль'],
    tag: 'No Sugar',
    bg: 'linear-gradient(150deg,#2C1810 0%,#4E2C12 35%,#7B3F00 65%,#BF6F00 100%)',
    accent: '#FFD54F', accentDark: '#F57F17',
    quote: 'Настоящий вкус рождается без сахара. Только белок, только польза.',
    question: 'Что если вкусное = полезное?',
  },
  {
    id: 'lamp',
    name: 'LED-светильник «Nordic»',
    shortName: 'Nordic LED',
    price: '2 490 ₽', oldPrice: '3 990 ₽', discount: 38,
    benefits: ['3 режима яркости', 'Сенсор касания', 'Мощность 15 Вт'],
    specs: ['40×15 см', 'Металл/акрил', '220V'],
    tag: 'Smart Touch',
    bg: 'linear-gradient(150deg,#050A0F 0%,#0D1B2A 35%,#1B2838 65%,#243B55 100%)',
    accent: '#F7DC6F', accentDark: '#F39C12',
    quote: 'Один жест меняет атмосферу. Nordic — это не просто свет, это настроение.',
    question: 'Что если один жест меняет настроение всей комнаты?',
  },
]

const STYLE_TABS: StyleKey[] = ['Продающий', 'Экспертный', 'Эмоциональный', 'Минималист', 'Сторителлинг']

function productToEdit(p: Product, photo = ''): EditData {
  return {
    name: p.name, price: p.price, oldPrice: p.oldPrice ?? '',
    benefit1: p.benefits[0], benefit2: p.benefits[1], benefit3: p.benefits[2],
    specs0: p.specs[0], specs1: p.specs[1], specs2: p.specs[2],
    photo,
  }
}

// ─── Card Layout ──────────────────────────────────────────────────────────────
// size = design unit (600 for preview/editor, 1200 for download)

function CardLayout({ p, style, photo, size, edit }: {
  p: Product; style: StyleKey; photo: string; size: number; edit?: EditData
}) {
  const sc = size / 600
  const f = (v: number) => v * sc

  const name    = edit?.name      ?? p.name
  const price   = edit?.price     ?? p.price
  const oldP    = edit?.oldPrice  ?? (p.oldPrice ?? '')
  const b1      = edit?.benefit1  ?? p.benefits[0]
  const b2      = edit?.benefit2  ?? p.benefits[1]
  const b3      = edit?.benefit3  ?? p.benefits[2]
  const sp0     = edit?.specs0    ?? p.specs[0]
  const sp1     = edit?.specs1    ?? p.specs[1]
  const sp2     = edit?.specs2    ?? p.specs[2]
  const img     = photo || edit?.photo || ''
  const disc    = p.discount

  const bgEl = img ? (
    <div style={{ position: 'absolute', inset: 0, backgroundImage: `url(${img})`, backgroundSize: 'cover', backgroundPosition: 'center' }} />
  ) : (
    <div style={{ position: 'absolute', inset: 0, background: p.bg }} />
  )

  // ── ПРОДАЮЩИЙ ──────────────────────────────────────────────────────────────
  if (style === 'Продающий') {
    return (
      <div style={{ position: 'relative', width: size, height: size, overflow: 'hidden', fontFamily: 'system-ui,sans-serif' }}>
        {bgEl}
        {/* Gradient overlays */}
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top,rgba(0,0,0,0.88) 0%,rgba(0,0,0,0.35) 55%,rgba(0,0,0,0.08) 100%)' }} />
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to right,rgba(0,0,0,0.25) 0%,transparent 60%)' }} />

        {/* Tag pill — top left */}
        <div style={{ position: 'absolute', top: f(18), left: f(18), background: 'rgba(255,255,255,0.18)', backdropFilter: 'blur(6px)', border: '1px solid rgba(255,255,255,0.35)', color: '#fff', fontSize: f(11), fontWeight: 600, padding: `${f(4)}px ${f(10)}px`, borderRadius: f(20) }}>
          {p.tag}
        </div>

        {/* Discount badge — top right */}
        {disc && (
          <div style={{ position: 'absolute', top: f(16), right: f(16), background: '#FF3B30', color: '#fff', fontWeight: 900, fontSize: f(22), lineHeight: 1, padding: `${f(8)}px ${f(14)}px`, borderRadius: f(8), boxShadow: `0 ${f(4)}px ${f(16)}px rgba(255,59,48,0.6)` }}>
            -{disc}%
          </div>
        )}

        {/* Bottom content */}
        <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: `${f(20)}px ${f(20)}px ${f(22)}px` }}>
          <div style={{ fontWeight: 800, fontSize: f(20), color: '#fff', lineHeight: 1.2, marginBottom: f(10), textShadow: '0 2px 8px rgba(0,0,0,0.6)' }}>{name}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: f(12), marginBottom: f(16) }}>
            <span style={{ fontWeight: 900, fontSize: f(34), color: '#fff', lineHeight: 1 }}>{price}</span>
            {oldP && <span style={{ fontSize: f(18), color: 'rgba(255,255,255,0.55)', textDecoration: 'line-through' }}>{oldP}</span>}
          </div>
          <div style={{ background: p.accent, color: '#fff', fontWeight: 700, fontSize: f(15), textAlign: 'center', padding: `${f(13)}px`, borderRadius: f(10), boxShadow: `0 ${f(4)}px ${f(18)}px ${p.accent}88` }}>
            Купить сейчас
          </div>
        </div>
      </div>
    )
  }

  // ── ЭКСПЕРТНЫЙ ─────────────────────────────────────────────────────────────
  if (style === 'Экспертный') {
    return (
      <div style={{ position: 'relative', width: size, height: size, overflow: 'hidden', fontFamily: 'system-ui,sans-serif' }}>
        {bgEl}
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.38)' }} />

        {/* Top-left name strip */}
        <div style={{ position: 'absolute', top: 0, left: 0, right: '40%', background: 'rgba(0,0,0,0.72)', backdropFilter: 'blur(4px)', padding: `${f(18)}px ${f(18)}px ${f(14)}px` }}>
          <div style={{ fontSize: f(9), fontWeight: 700, color: p.accent, letterSpacing: '0.1em', marginBottom: f(5), textTransform: 'uppercase' }}>ХАРАКТЕРИСТИКИ</div>
          <div style={{ fontWeight: 700, fontSize: f(14), color: '#fff', lineHeight: 1.25 }}>{name}</div>
          <div style={{ fontWeight: 800, fontSize: f(20), color: p.accent, marginTop: f(5) }}>{price}</div>
        </div>

        {/* Right specs panel */}
        <div style={{ position: 'absolute', top: 0, right: 0, width: '42%', bottom: 0, background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(8px)', borderLeft: `2px solid ${p.accent}55`, padding: `${f(18)}px ${f(14)}px` }}>
          <div style={{ fontSize: f(8), fontWeight: 700, color: p.accent, letterSpacing: '0.1em', marginBottom: f(12), textTransform: 'uppercase' }}>SPECS</div>
          {[['Размер / Объём', sp0], ['Состав / Материал', sp1], ['Доп. параметр', sp2]].map(([k, v], i) => (
            <div key={i} style={{ borderBottom: `1px solid rgba(255,255,255,0.12)`, padding: `${f(8)}px 0`, marginBottom: f(2) }}>
              <div style={{ fontSize: f(8), color: 'rgba(255,255,255,0.5)', marginBottom: f(2) }}>{k}</div>
              <div style={{ fontSize: f(11), fontWeight: 600, color: '#fff' }}>{v}</div>
            </div>
          ))}
          <div style={{ marginTop: f(14) }}>
            <div style={{ fontSize: f(8), color: p.accent, fontWeight: 700, marginBottom: f(6), textTransform: 'uppercase' }}>ПРЕИМУЩЕСТВА</div>
            {[b1, b2, b3].filter(Boolean).map((b, i) => (
              <div key={i} style={{ display: 'flex', gap: f(5), alignItems: 'flex-start', marginBottom: f(5) }}>
                <span style={{ color: p.accent, fontWeight: 700, fontSize: f(11), flexShrink: 0 }}>✓</span>
                <span style={{ fontSize: f(10), color: '#fff', lineHeight: 1.3 }}>{b}</span>
              </div>
            ))}
          </div>
          {disc && (
            <div style={{ position: 'absolute', bottom: f(14), right: f(14), background: '#FF3B30', color: '#fff', fontWeight: 900, fontSize: f(16), padding: `${f(6)}px ${f(10)}px`, borderRadius: f(6) }}>-{disc}%</div>
          )}
        </div>

        {/* Bottom benefit icons */}
        <div style={{ position: 'absolute', bottom: 0, left: 0, right: '42%', background: 'linear-gradient(to top,rgba(0,0,0,0.82) 0%,transparent 100%)', padding: `${f(20)}px ${f(14)}px ${f(14)}px` }}>
          {[b1, b2].map((b, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: f(6), marginBottom: f(5) }}>
              <div style={{ width: f(5), height: f(5), background: p.accent, borderRadius: '50%', flexShrink: 0 }} />
              <span style={{ fontSize: f(10), color: 'rgba(255,255,255,0.85)' }}>{b}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ── ЭМОЦИОНАЛЬНЫЙ ─────────────────────────────────────────────────────────
  if (style === 'Эмоциональный') {
    return (
      <div style={{ position: 'relative', width: size, height: size, overflow: 'hidden', fontFamily: 'system-ui,sans-serif' }}>
        {bgEl}
        {/* Strong bottom-heavy gradient */}
        <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to top,rgba(0,0,0,0.92) 0%,rgba(0,0,0,0.5) 45%,rgba(0,0,0,0.1) 80%,transparent 100%)' }} />

        {/* Accent glow top */}
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: f(3), background: p.accent }} />

        {/* Center name */}
        <div style={{ position: 'absolute', top: '28%', left: 0, right: 0, textAlign: 'center', padding: `0 ${f(24)}px` }}>
          <div style={{ fontWeight: 900, fontSize: f(26), color: '#fff', lineHeight: 1.15, textShadow: '0 4px 20px rgba(0,0,0,0.7)', letterSpacing: '-0.01em' }}>{name}</div>
          <div style={{ marginTop: f(10), fontWeight: 700, fontSize: f(28), color: p.accent, textShadow: `0 0 ${f(20)}px ${p.accent}88` }}>{price}</div>
        </div>

        {/* Bottom benefit pills */}
        <div style={{ position: 'absolute', bottom: f(24), left: 0, right: 0, display: 'flex', justifyContent: 'center', gap: f(8), flexWrap: 'wrap', padding: `0 ${f(16)}px` }}>
          {[b1, b2, b3].filter(Boolean).map((b, i) => (
            <div key={i} style={{ background: 'rgba(255,255,255,0.15)', backdropFilter: 'blur(8px)', border: `1px solid ${p.accent}66`, color: '#fff', fontSize: f(10), fontWeight: 600, padding: `${f(5)}px ${f(11)}px`, borderRadius: f(20) }}>
              {b}
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ── МИНИМАЛИСТ ─────────────────────────────────────────────────────────────
  if (style === 'Минималист') {
    return (
      <div style={{ position: 'relative', width: size, height: size, overflow: 'hidden', fontFamily: 'system-ui,sans-serif', display: 'flex' }}>
        {/* Left 62% — photo */}
        <div style={{ position: 'relative', width: '62%', flexShrink: 0, overflow: 'hidden' }}>
          {bgEl}
          <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(to right,rgba(0,0,0,0.05) 0%,rgba(0,0,0,0.2) 100%)' }} />
          {/* Tag */}
          <div style={{ position: 'absolute', top: f(14), left: f(14), background: 'rgba(0,0,0,0.55)', color: '#fff', fontSize: f(9), fontWeight: 700, padding: `${f(3)}px ${f(8)}px`, borderRadius: f(4), letterSpacing: '0.06em' }}>
            ХИТ · {p.tag}
          </div>
        </div>

        {/* Right 38% — info panel */}
        <div style={{ flex: 1, background: '#fafafa', display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: `${f(20)}px ${f(16)}px`, position: 'relative' }}>
          {/* Accent line left */}
          <div style={{ position: 'absolute', top: f(24), bottom: f(24), left: 0, width: f(3), background: p.accent }} />

          <div style={{ fontSize: f(9), fontWeight: 700, color: p.accentDark, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: f(8) }}>NEW IN</div>
          <div style={{ fontWeight: 300, fontSize: f(14), color: '#111', lineHeight: 1.35, marginBottom: f(14) }}>{name}</div>
          <div style={{ fontWeight: 700, fontSize: f(24), color: '#111', marginBottom: f(4) }}>{price}</div>
          {oldP && <div style={{ fontSize: f(12), color: '#aaa', textDecoration: 'line-through', marginBottom: f(12) }}>{oldP}</div>}

          <div style={{ borderTop: `1px solid #e8e8e8`, paddingTop: f(10), marginTop: f(2) }}>
            {[b1, b2].map((b, i) => (
              <div key={i} style={{ display: 'flex', gap: f(5), alignItems: 'center', marginBottom: f(5) }}>
                <div style={{ width: f(4), height: f(4), background: p.accent, borderRadius: '50%', flexShrink: 0 }} />
                <span style={{ fontSize: f(10), color: '#555' }}>{b}</span>
              </div>
            ))}
          </div>

          {disc && (
            <div style={{ position: 'absolute', bottom: f(14), right: f(14), background: '#FF3B30', color: '#fff', fontWeight: 700, fontSize: f(10), padding: `${f(3)}px ${f(7)}px`, borderRadius: f(4) }}>-{disc}%</div>
          )}
        </div>
      </div>
    )
  }

  // ── СТОРИТЕЛЛИНГ ─────────────────────────────────────────────────────────
  if (style === 'Сторителлинг') {
    return (
      <div style={{ position: 'relative', width: size, height: size, overflow: 'hidden', fontFamily: 'system-ui,sans-serif' }}>
        {bgEl}
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.72)' }} />

        {/* До/После top band */}
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: f(120), display: 'flex' }}>
          <div style={{ flex: 1, borderRight: `2px solid ${p.accent}`, position: 'relative', overflow: 'hidden' }}>
            {bgEl}
            <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)', filter: 'grayscale(0.6)' }} />
            <div style={{ position: 'absolute', bottom: f(6), left: 0, right: 0, textAlign: 'center', fontSize: f(9), fontWeight: 700, color: 'rgba(255,255,255,0.7)', letterSpacing: '0.12em' }}>ДО</div>
          </div>
          <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
            {img
              ? <div style={{ position: 'absolute', inset: 0, backgroundImage: `url(${img})`, backgroundSize: 'cover', backgroundPosition: 'center', filter: 'brightness(1.15) saturate(1.2)' }} />
              : <div style={{ position: 'absolute', inset: 0, background: p.bg, filter: 'brightness(1.2) saturate(1.2)' }} />
            }
            <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(135deg,${p.accent}33 0%,transparent 100%)` }} />
            <div style={{ position: 'absolute', bottom: f(6), left: 0, right: 0, textAlign: 'center', fontSize: f(9), fontWeight: 700, color: p.accent, letterSpacing: '0.12em' }}>ПОСЛЕ</div>
          </div>
        </div>

        {/* Main content */}
        <div style={{ position: 'absolute', top: f(128), left: 0, right: 0, bottom: 0, padding: `${f(16)}px ${f(20)}px ${f(20)}px`, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          {/* Question heading */}
          <div>
            <div style={{ fontWeight: 800, fontSize: f(18), color: '#fff', lineHeight: 1.25, marginBottom: f(10) }}>
              {p.question}
            </div>
            {/* Quote */}
            <div style={{ borderLeft: `3px solid ${p.accent}`, paddingLeft: f(10), marginBottom: f(14) }}>
              <div style={{ fontSize: f(11), color: 'rgba(255,255,255,0.75)', fontStyle: 'italic', lineHeight: 1.5 }}>«{p.quote}»</div>
            </div>
          </div>

          {/* Benefits row */}
          <div style={{ display: 'flex', gap: f(6), flexWrap: 'wrap', marginBottom: f(12) }}>
            {[b1, b2, b3].filter(Boolean).map((b, i) => (
              <div key={i} style={{ background: `${p.accent}22`, border: `1px solid ${p.accent}55`, color: '#fff', fontSize: f(9), fontWeight: 600, padding: `${f(3)}px ${f(8)}px`, borderRadius: f(20) }}>{b}</div>
            ))}
          </div>

          {/* Price */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: f(10) }}>
            <span style={{ fontWeight: 900, fontSize: f(28), color: '#fff' }}>{price}</span>
            {oldP && <span style={{ fontSize: f(15), color: 'rgba(255,255,255,0.4)', textDecoration: 'line-through' }}>{oldP}</span>}
          </div>
        </div>
      </div>
    )
  }

  return null
}

// ─── Preview Cell ─────────────────────────────────────────────────────────────

function PreviewCell({ p, style, onClick }: { p: Product; style: StyleKey; onClick: () => void }) {
  const CARD = 600
  const PRV  = 214
  const scale = PRV / CARD

  return (
    <button
      onClick={onClick}
      style={{ width: 240, height: 318, flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', background: '#09090B', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: '12px 12px 10px', cursor: 'pointer', transition: 'border-color 0.15s,transform 0.15s', position: 'relative', overflow: 'hidden' }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = '#7C3AED'; e.currentTarget.style.transform = 'translateY(-2px)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)'; e.currentTarget.style.transform = 'translateY(0)' }}
    >
      <div style={{ width: PRV, height: PRV, overflow: 'hidden', borderRadius: 6, flexShrink: 0 }}>
        <div style={{ transform: `scale(${scale})`, transformOrigin: 'top left', width: CARD, height: CARD, pointerEvents: 'none' }}>
          <CardLayout p={p} style={style} photo="" size={CARD} />
        </div>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, fontWeight: 600, color: '#FFFFFF', textAlign: 'center', lineHeight: 1.3 }}>{p.shortName}</div>
      <div style={{ fontSize: 10, color: '#6B6B72', marginTop: 2 }}>{p.price}{p.oldPrice ? ' · ' + p.oldPrice : ''}</div>
      {/* Hover overlay */}
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(110,106,252,0.08)', opacity: 0, transition: 'opacity 0.15s', borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 600, color: '#7C3AED' }}
        onMouseEnter={e => { e.currentTarget.style.opacity = '1' }}
        onMouseLeave={e => { e.currentTarget.style.opacity = '0' }}
      >
        Открыть →
      </div>
    </button>
  )
}

// ─── Modal ────────────────────────────────────────────────────────────────────

function Modal({ p, style, onUse, onDownload, onClose }: {
  p: Product; style: StyleKey
  onUse: () => void; onDownload: () => void; onClose: () => void
}) {
  // Close on backdrop click
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', backdropFilter: 'blur(8px)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{ background: '#09090B', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 20, overflow: 'hidden', maxWidth: 900, width: '100%', display: 'flex', gap: 0 }}>
        {/* Card preview */}
        <div style={{ width: 520, flexShrink: 0, background: '#0D0D0D' }}>
          <CardLayout p={p} style={style} photo="" size={520} />
        </div>
        {/* Info panel */}
        <div style={{ flex: 1, padding: 28, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#7C3AED', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 8 }}>{style}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#FFFFFF', lineHeight: 1.3, marginBottom: 6 }}>{p.name}</div>
            <div style={{ fontSize: 24, fontWeight: 900, color: '#FFFFFF', marginBottom: 4 }}>{p.price}</div>
            {p.oldPrice && <div style={{ fontSize: 14, color: '#6B6B72', textDecoration: 'line-through', marginBottom: 16 }}>{p.oldPrice}</div>}

            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 11, color: '#6B6B72', fontWeight: 700, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Преимущества</div>
              {p.benefits.map((b, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ color: p.accent, fontWeight: 700 }}>✓</span>
                  <span style={{ fontSize: 13, color: '#FFFFFF' }}>{b}</span>
                </div>
              ))}
            </div>

            <div>
              <div style={{ fontSize: 11, color: '#6B6B72', fontWeight: 700, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Характеристики</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {p.specs.map((s, i) => (
                  <span key={i} style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)', color: '#71717A', fontSize: 11, padding: '3px 9px', borderRadius: 6 }}>{s}</span>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <button onClick={onUse} style={{ padding: '12px', background: '#7C3AED', color: '#fff', border: 'none', borderRadius: 10, fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
              Использовать шаблон
            </button>
            <button onClick={onDownload} style={{ padding: '10px', background: '#111113', color: '#FFFFFF', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10, fontSize: 13, fontWeight: 500, cursor: 'pointer' }}>
              Скачать PNG
            </button>
            <button onClick={onClose} style={{ padding: '8px', background: 'none', color: '#6B6B72', border: 'none', fontSize: 13, cursor: 'pointer' }}>
              Закрыть
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Editor ───────────────────────────────────────────────────────────────────

function Editor({ p, style, initialData, onClose, onDownload }: {
  p: Product; style: StyleKey; initialData: EditData
  onClose: () => void; onDownload: (d: EditData) => void
}) {
  const [data, setData] = useState<EditData>(initialData)
  const fileRef = useRef<HTMLInputElement>(null)

  function set<K extends keyof EditData>(k: K, v: EditData[K]) { setData(d => ({ ...d, [k]: v })) }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    const reader = new FileReader()
    reader.onload = () => set('photo', reader.result as string)
    reader.readAsDataURL(file)
  }

  const inp: React.CSSProperties = { width: '100%', background: '#18181B', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#FFFFFF', outline: 'none', boxSizing: 'border-box' }
  const lbl: React.CSSProperties = { fontSize: 11, color: '#71717A', marginBottom: 4, display: 'block', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }
  const fld: React.CSSProperties = { marginBottom: 12 }

  return (
    <div style={{ background: '#09090B', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 16, overflow: 'hidden', marginTop: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 24px', borderBottom: '1px solid rgba(255,255,255,0.08)', background: '#111113' }}>
        <div>
          <span style={{ fontSize: 15, fontWeight: 700, color: '#FFFFFF' }}>Редактор — </span>
          <span style={{ fontSize: 15, fontWeight: 700, color: '#7C3AED' }}>{style}</span>
          <span style={{ fontSize: 13, color: '#71717A', marginLeft: 10 }}>{p.shortName}</span>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#71717A', cursor: 'pointer', fontSize: 20, lineHeight: 1, padding: '2px 6px' }}>✕</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '600px 1fr' }}>
        {/* Live preview */}
        <div style={{ background: '#0D0D0D', display: 'flex', justifyContent: 'center', alignItems: 'flex-start', padding: 24 }}>
          <div style={{ width: 552, height: 552, borderRadius: 12, overflow: 'hidden' }}>
            <CardLayout p={p} style={style} photo={data.photo} size={552} edit={data} />
          </div>
        </div>

        {/* Form */}
        <div style={{ padding: 24, overflowY: 'auto', maxHeight: 600 }}>
          {/* Photo upload */}
          <div style={fld}>
            <label style={lbl}>Фото товара</label>
            {data.photo && (
              <div style={{ marginBottom: 8, height: 80, borderRadius: 8, overflow: 'hidden', background: '#18181B' }}>
                <img src={data.photo} style={{ width: '100%', height: '100%', objectFit: 'cover' }} alt="" />
              </div>
            )}
            <button onClick={() => fileRef.current?.click()} style={{ ...inp, textAlign: 'center', cursor: 'pointer', color: '#71717A', border: '1px dashed rgba(110,106,252,0.5)', padding: '10px' }}>
              {data.photo ? '↑ Заменить фото' : '↑ Загрузить фото товара'}
            </button>
            <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleFile} />
            {data.photo && (
              <button onClick={() => set('photo', '')} style={{ marginTop: 4, background: 'none', border: 'none', color: '#EF4444', fontSize: 11, cursor: 'pointer', padding: 0 }}>
                Удалить фото
              </button>
            )}
          </div>

          <div style={fld}><label style={lbl}>Название товара</label><input style={inp} value={data.name} onChange={e => set('name', e.target.value)} /></div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
            <div><label style={lbl}>Цена</label><input style={inp} value={data.price} onChange={e => set('price', e.target.value)} /></div>
            <div><label style={lbl}>Старая цена</label><input style={inp} value={data.oldPrice} onChange={e => set('oldPrice', e.target.value)} placeholder="Необязательно" /></div>
          </div>

          <div style={fld}><label style={lbl}>Преимущество 1</label><input style={inp} value={data.benefit1} onChange={e => set('benefit1', e.target.value)} /></div>
          <div style={fld}><label style={lbl}>Преимущество 2</label><input style={inp} value={data.benefit2} onChange={e => set('benefit2', e.target.value)} /></div>
          <div style={fld}><label style={lbl}>Преимущество 3</label><input style={inp} value={data.benefit3} onChange={e => set('benefit3', e.target.value)} /></div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
            <div><label style={lbl}>Хар-ка 1</label><input style={inp} value={data.specs0} onChange={e => set('specs0', e.target.value)} /></div>
            <div><label style={lbl}>Хар-ка 2</label><input style={inp} value={data.specs1} onChange={e => set('specs1', e.target.value)} /></div>
            <div><label style={lbl}>Хар-ка 3</label><input style={inp} value={data.specs2} onChange={e => set('specs2', e.target.value)} /></div>
          </div>

          <button
            onClick={() => onDownload(data)}
            style={{ width: '100%', padding: '13px', background: '#7C3AED', color: '#fff', border: 'none', borderRadius: 10, fontSize: 14, fontWeight: 600, cursor: 'pointer', marginTop: 8 }}
          >
            Скачать карточку 1200×1200 PNG
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Canvas Download ──────────────────────────────────────────────────────────

async function downloadCardCanvas(p: Product, style: StyleKey, edit: EditData) {
  const SIZE = 1200
  const BASE = 600
  const canvas = document.createElement('canvas')
  canvas.width = SIZE; canvas.height = SIZE
  const ctx = canvas.getContext('2d')!
  const sc = SIZE / BASE
  ctx.scale(sc, sc)

  const name   = edit.name   || p.name
  const price  = edit.price  || p.price
  const oldP   = edit.oldPrice || (p.oldPrice ?? '')
  const b1     = edit.benefit1 || p.benefits[0]
  const b2     = edit.benefit2 || p.benefits[1]
  const b3     = edit.benefit3 || p.benefits[2]
  const sp0    = edit.specs0 || p.specs[0]
  const sp1    = edit.specs1 || p.specs[1]
  const disc   = p.discount
  const px     = (v: number) => v
  const img    = edit.photo

  function grad(stops: [number, string][], x0=0, y0=0, x1=0, y1=BASE) {
    const g = ctx.createLinearGradient(x0,y0,x1,y1)
    stops.forEach(([s,c]) => g.addColorStop(s,c))
    return g
  }

  function rr(x: number, y: number, w: number, h: number, r: number) {
    ctx.beginPath()
    ctx.moveTo(x+r,y); ctx.lineTo(x+w-r,y); ctx.quadraticCurveTo(x+w,y,x+w,y+r)
    ctx.lineTo(x+w,y+h-r); ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h)
    ctx.lineTo(x+r,y+h); ctx.quadraticCurveTo(x,y+h,x,y+h-r)
    ctx.lineTo(x,y+r); ctx.quadraticCurveTo(x,y,x+r,y); ctx.closePath()
  }

  function ftm(text: string, x: number, y: number, maxW: number) {
    let s = text
    while (ctx.measureText(s).width > maxW && s.length > 2) s = s.slice(0,-1)
    if (s !== text) s = s.slice(0,-1) + '…'
    ctx.fillText(s, x, y)
  }

  async function drawImg(x: number, y: number, w: number, h: number, src: string, filter?: string) {
    return new Promise<void>(resolve => {
      const i = new Image(); i.crossOrigin = 'anonymous'
      i.onload = () => {
        if (filter) { ctx.save(); ctx.filter = filter }
        ctx.drawImage(i, x, y, w, h)
        if (filter) ctx.restore()
        resolve()
      }
      i.onerror = () => resolve(); i.src = src
    })
  }

  // Parse gradient string and draw as fillStyle
  function drawGrad(x: number, y: number, w: number, h: number) {
    // Use color stops parsed from p.bg
    const stops: [number, string][] = [
      [0, p.bg.includes('#') ? p.bg.match(/#[0-9A-Fa-f]{6}/g)?.[0] || '#333' : '#333'],
      [0.5, p.bg.match(/#[0-9A-Fa-f]{6}/g)?.[1] || '#555'],
      [1, p.bg.match(/#[0-9A-Fa-f]{6}/g)?.[p.bg.match(/#[0-9A-Fa-f]{6}/g)!.length - 1] || '#777'],
    ]
    const g = grad(stops, x, y, x, y+h)
    ctx.fillStyle = g; ctx.fillRect(x, y, w, h)
  }

  async function drawBg(x: number, y: number, w: number, h: number, filter?: string) {
    if (img) { await drawImg(x, y, w, h, img, filter) }
    else { drawGrad(x, y, w, h) }
  }

  async function drawOverlay(x: number, y: number, w: number, h: number, bg: string) {
    ctx.fillStyle = bg; ctx.fillRect(x, y, w, h)
  }

  if (style === 'Продающий') {
    await drawBg(0, 0, BASE, BASE)
    ctx.fillStyle = grad([[0,'rgba(0,0,0,0.88)'], [0.55,'rgba(0,0,0,0.35)'], [1,'rgba(0,0,0,0.08)']],0,BASE,0,0)
    ctx.fillRect(0,0,BASE,BASE)
    // Tag
    ctx.fillStyle = 'rgba(255,255,255,0.18)'; rr(px(18),px(18),px(90),px(24),px(12)); ctx.fill()
    ctx.font = `600 ${px(11)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'
    ctx.fillText(p.tag, px(28), px(30))
    // Badge
    if (disc) {
      ctx.fillStyle = '#FF3B30'; rr(BASE-px(16)-px(90),px(16),px(90),px(44),px(8)); ctx.fill()
      ctx.font = `900 ${px(22)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText(`-${disc}%`, BASE-px(16)-px(45), px(38))
    }
    // Name
    ctx.font = `800 ${px(22)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
    ftm(name, px(20), px(490), BASE - px(40))
    // Prices
    ctx.font = `900 ${px(34)}px system-ui`; ctx.fillStyle = '#fff'
    ctx.fillText(price, px(20), px(534))
    if (oldP) {
      const pw = ctx.measureText(price).width
      ctx.font = `${px(18)}px system-ui`; ctx.fillStyle = 'rgba(255,255,255,0.55)'
      ctx.fillText(oldP, px(20)+pw+px(14), px(534))
      const ow = ctx.measureText(oldP).width
      ctx.strokeStyle = 'rgba(255,255,255,0.55)'; ctx.lineWidth = 1.5
      ctx.beginPath(); ctx.moveTo(px(20)+pw+px(14), px(528)); ctx.lineTo(px(20)+pw+px(14)+ow, px(528)); ctx.stroke()
    }
    // Button
    ctx.fillStyle = p.accent; rr(px(20),px(548),BASE-px(40),px(44),px(10)); ctx.fill()
    ctx.font = `700 ${px(16)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillText('Купить сейчас', BASE/2, px(570))

  } else if (style === 'Экспертный') {
    await drawBg(0, 0, BASE, BASE)
    ctx.fillStyle = 'rgba(0,0,0,0.38)'; ctx.fillRect(0,0,BASE,BASE)
    // Top-left name strip
    ctx.fillStyle = 'rgba(0,0,0,0.72)'; ctx.fillRect(0,0,BASE*0.6,px(90))
    ctx.font = `700 ${px(9)}px system-ui`; ctx.fillStyle = p.accent; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
    ctx.fillText('ХАРАКТЕРИСТИКИ', px(18), px(22))
    ctx.font = `700 ${px(14)}px system-ui`; ctx.fillStyle = '#fff'
    ftm(name, px(18), px(46), BASE*0.55)
    ctx.font = `800 ${px(20)}px system-ui`; ctx.fillStyle = p.accent
    ctx.fillText(price, px(18), px(76))
    // Right specs panel
    ctx.fillStyle = 'rgba(0,0,0,0.78)'; ctx.fillRect(BASE*0.58, 0, BASE*0.42, BASE)
    ctx.strokeStyle = p.accent + '55'; ctx.lineWidth = 2
    ctx.beginPath(); ctx.moveTo(BASE*0.58, 0); ctx.lineTo(BASE*0.58, BASE); ctx.stroke()
    const specRX = BASE*0.58 + px(14)
    ctx.font = `700 ${px(8)}px system-ui`; ctx.fillStyle = p.accent
    ctx.fillText('SPECS', specRX, px(20))
    ;[[sp0,'Объём / Размер'],[sp1,'Материал'],[b1,'Преимущество']].forEach(([v,k],i) => {
      const yy = px(40) + i * px(60)
      ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(specRX, yy-px(4)); ctx.lineTo(BASE-px(14), yy-px(4)); ctx.stroke()
      ctx.font = `${px(8)}px system-ui`; ctx.fillStyle = 'rgba(255,255,255,0.5)'
      ctx.fillText(k, specRX, yy+px(10))
      ctx.font = `600 ${px(11)}px system-ui`; ctx.fillStyle = '#fff'
      ftm(v, specRX, yy+px(26), BASE*0.38)
    })
    if (disc) {
      ctx.fillStyle = '#FF3B30'; rr(BASE-px(56), BASE-px(40), px(44), px(26), px(4)); ctx.fill()
      ctx.font = `700 ${px(10)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText(`-${disc}%`, BASE-px(34), BASE-px(27))
    }

  } else if (style === 'Эмоциональный') {
    await drawBg(0, 0, BASE, BASE)
    ctx.fillStyle = grad([[0,'rgba(0,0,0,0.92)'],[0.45,'rgba(0,0,0,0.5)'],[0.8,'rgba(0,0,0,0.1)'],[1,'transparent']],0,BASE,0,0)
    ctx.fillRect(0,0,BASE,BASE)
    ctx.fillStyle = p.accent; ctx.fillRect(0,0,BASE,px(3))
    ctx.font = `900 ${px(28)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'
    ftm(name, BASE/2, px(424), BASE-px(40))
    ctx.font = `700 ${px(30)}px system-ui`; ctx.fillStyle = p.accent
    ctx.fillText(price, BASE/2, px(468))
    // Benefit pills
    const pills = [b1,b2,b3].filter(Boolean)
    pills.forEach((b, i) => {
      const bw = Math.min(ctx.measureText(b).width + px(20), px(165))
      const bx = BASE/2 - (pills.length*bw + (pills.length-1)*px(8))/2 + i*(bw+px(8))
      ctx.fillStyle = 'rgba(255,255,255,0.18)'; rr(bx, px(496), bw, px(26), px(13)); ctx.fill()
      ctx.strokeStyle = p.accent+'66'; ctx.lineWidth = 1; rr(bx, px(496), bw, px(26), px(13)); ctx.stroke()
      ctx.font = `600 ${px(10)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ftm(b, bx+bw/2, px(509), bw-px(8))
    })

  } else if (style === 'Минималист') {
    // Photo left 62%
    await drawBg(0, 0, BASE*0.62, BASE)
    // Right panel
    ctx.fillStyle = '#fafafa'; ctx.fillRect(BASE*0.62, 0, BASE*0.38, BASE)
    ctx.fillStyle = p.accent; ctx.fillRect(BASE*0.62, px(24), px(3), BASE-px(48))
    const rx = BASE*0.62 + px(16)
    ctx.font = `700 ${px(9)}px system-ui`; ctx.fillStyle = p.accentDark; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
    ctx.fillText('NEW IN', rx, px(90))
    ctx.font = `300 ${px(14)}px system-ui`; ctx.fillStyle = '#111'
    ftm(name, rx, px(116), BASE*0.34)
    ftm(name.split(' ').slice(2).join(' ') || '', rx, px(138), BASE*0.34)
    ctx.font = `700 ${px(24)}px system-ui`; ctx.fillStyle = '#111'
    ctx.fillText(price, rx, px(182))
    if (oldP) {
      ctx.font = `${px(13)}px system-ui`; ctx.fillStyle = '#aaa'
      ctx.fillText(oldP, rx, px(202))
      ctx.strokeStyle = '#aaa'; ctx.lineWidth = 1
      const ow = ctx.measureText(oldP).width
      ctx.beginPath(); ctx.moveTo(rx, px(197)); ctx.lineTo(rx+ow, px(197)); ctx.stroke()
    }
    ctx.strokeStyle = '#e8e8e8'; ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(rx, px(220)); ctx.lineTo(BASE-px(14), px(220)); ctx.stroke()
    ;[b1, b2].forEach((b,i) => {
      ctx.fillStyle = p.accent; ctx.beginPath(); ctx.arc(rx+px(4), px(238)+i*px(26), px(3), 0, Math.PI*2); ctx.fill()
      ctx.font = `${px(10)}px system-ui`; ctx.fillStyle = '#555'
      ftm(b, rx+px(12), px(242)+i*px(26), BASE*0.32)
    })
    if (disc) {
      ctx.fillStyle = '#FF3B30'; rr(BASE-px(42),BASE-px(32),px(30),px(18),px(3)); ctx.fill()
      ctx.font = `700 ${px(10)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText(`-${disc}%`, BASE-px(27), BASE-px(23))
    }

  } else if (style === 'Сторителлинг') {
    await drawBg(0, 0, BASE, BASE)
    ctx.fillStyle = 'rgba(0,0,0,0.72)'; ctx.fillRect(0,0,BASE,BASE)
    // Before/After top
    await drawBg(0, 0, BASE/2, px(120), 'grayscale(0.6) brightness(0.6)')
    await drawBg(BASE/2, 0, BASE/2, px(120), 'brightness(1.15) saturate(1.2)')
    ctx.fillStyle = p.accent; ctx.fillRect(BASE/2-1, 0, 2, px(120))
    ctx.font = `700 ${px(9)}px system-ui`; ctx.fillStyle = 'rgba(255,255,255,0.7)'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'
    ctx.fillText('ДО', BASE/4, px(112))
    ctx.fillStyle = p.accent; ctx.fillText('ПОСЛЕ', BASE*3/4, px(112))
    // Question
    ctx.font = `800 ${px(19)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'left'
    const qWords = p.question.split(' ')
    let qLine = '', qY = px(148)
    qWords.forEach(w => {
      const test = qLine ? qLine+' '+w : w
      if (ctx.measureText(test).width > BASE-px(40) && qLine) { ctx.fillText(qLine, px(20), qY); qLine = w; qY += px(28) }
      else qLine = test
    })
    if (qLine) { ctx.fillText(qLine, px(20), qY); qY += px(28) }
    // Quote
    ctx.strokeStyle = p.accent; ctx.lineWidth = 3
    ctx.beginPath(); ctx.moveTo(px(20), qY+px(8)); ctx.lineTo(px(20), qY+px(54)); ctx.stroke()
    ctx.font = `italic ${px(11)}px system-ui`; ctx.fillStyle = 'rgba(255,255,255,0.75)'; ctx.textAlign = 'left'
    const qText = '«' + p.quote + '»'
    const qWords2 = qText.split(' ')
    let qL2 = '', qY2 = qY+px(22)
    qWords2.forEach(w => {
      const t = qL2 ? qL2+' '+w : w
      if (ctx.measureText(t).width > BASE-px(50) && qL2) { ctx.fillText(qL2, px(30), qY2); qL2 = w; qY2 += px(18) }
      else qL2 = t
    })
    if (qL2) ctx.fillText(qL2, px(30), qY2)
    // Benefits
    ;[b1,b2,b3].filter(Boolean).forEach((b,i) => {
      ctx.fillStyle = p.accent+'33'; rr(px(20)+i*(BASE-px(40))/3+px(2), px(480), (BASE-px(56))/3, px(28), px(14)); ctx.fill()
      ctx.strokeStyle = p.accent+'66'; ctx.lineWidth = 1; rr(px(20)+i*(BASE-px(40))/3+px(2), px(480), (BASE-px(56))/3, px(28), px(14)); ctx.stroke()
      ctx.font = `600 ${px(9)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ftm(b, px(20)+i*(BASE-px(40))/3+px(2)+(BASE-px(56))/6, px(494), (BASE-px(56))/3-px(8))
    })
    // Price
    ctx.font = `900 ${px(28)}px system-ui`; ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'
    ctx.fillText(price, px(20), px(550))
    if (oldP) {
      ctx.font = `${px(15)}px system-ui`; ctx.fillStyle = 'rgba(255,255,255,0.4)'
      const pw3 = ctx.measureText(price).width
      ctx.fillText(oldP, px(20)+pw3+px(14), px(550))
      const ow3 = ctx.measureText(oldP).width
      ctx.strokeStyle = 'rgba(255,255,255,0.4)'; ctx.lineWidth = 1.5
      ctx.beginPath(); ctx.moveTo(px(20)+pw3+px(14), px(544)); ctx.lineTo(px(20)+pw3+px(14)+ow3, px(544)); ctx.stroke()
    }
  }

  ctx.setTransform(1,0,0,1,0,0)
  const link = document.createElement('a')
  link.download = `card_${edit.name.replace(/\s+/g,'_') || p.id}.png`
  link.href = canvas.toDataURL('image/png')
  link.click()
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SeoCardsPage() {
  const [activeStyle, setActiveStyle] = useState<StyleKey>('Продающий')
  const [modal, setModal]   = useState<{ p: Product; style: StyleKey } | null>(null)
  const [editor, setEditor] = useState<{ p: Product; style: StyleKey; init: EditData } | null>(null)

  function openModal(p: Product) { setModal({ p, style: activeStyle }) }
  function openEditor(p: Product) {
    setEditor({ p, style: modal?.style ?? activeStyle, init: productToEdit(p) })
    setModal(null)
  }
  async function downloadFromModal(p: Product) {
    await downloadCardCanvas(p, modal?.style ?? activeStyle, productToEdit(p))
  }
  async function downloadFromEditor(d: EditData) {
    if (!editor) return
    await downloadCardCanvas(editor.p, editor.style, d)
  }

  return (
    <AppShell>
      <div style={{ padding: '32px' }}>
        {/* Header */}
        <div style={{ marginBottom: 28 }}>
          <p style={{ fontSize: 11, fontWeight: 600, color: '#6B6B72', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>ИНСТРУМЕНТЫ</p>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#FFFFFF', marginBottom: 4 }}>SEO-карточки</h1>
          <p style={{ fontSize: 13, color: '#71717A' }}>6 реальных товаров · 5 стилей · 30 шаблонов · фото поверх текста</p>
        </div>

        {/* Style tabs */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 24, flexWrap: 'wrap' }}>
          {STYLE_TABS.map(s => (
            <button
              key={s}
              onClick={() => { setActiveStyle(s); setEditor(null) }}
              style={{
                padding: '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', borderRadius: 8,
                background: activeStyle === s ? 'rgba(110,106,252,0.15)' : '#111113',
                border: `1px solid ${activeStyle === s ? 'rgba(110,106,252,0.4)' : 'rgba(255,255,255,0.08)'}`,
                color: activeStyle === s ? '#7C3AED' : '#71717A',
                transition: 'all 0.15s',
              }}
            >
              {s}
            </button>
          ))}
        </div>

        {/* Style description */}
        <div style={{ marginBottom: 20, padding: '10px 16px', background: '#111113', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, fontSize: 12, color: '#71717A' }}>
          {activeStyle === 'Продающий'    && '🔥 Конверсия: скидочный бейдж, цена крупно, кнопка «Купить» — для WB и Ozon'}
          {activeStyle === 'Экспертный'   && '📊 Характеристики: specs-таблица справа, преимущества, минимум цвета — для технических товаров'}
          {activeStyle === 'Эмоциональный'&& '✨ Фото на весь экран: название и цена поверх, benefit-пилюли внизу — для lifestyle-товаров'}
          {activeStyle === 'Минималист'   && '🎯 Фото слева, данные справа на светлом фоне — премиум-эстетика'}
          {activeStyle === 'Сторителлинг' && '📖 История товара: До/После, вопрос-заголовок, цитата создателей — для сторибрендинга'}
        </div>

        {/* Gallery — 6 product cards */}
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
          {PRODUCTS.map(p => (
            <PreviewCell key={p.id} p={p} style={activeStyle} onClick={() => openModal(p)} />
          ))}
        </div>

        {/* Editor */}
        {editor && (
          <Editor
            p={editor.p}
            style={editor.style}
            initialData={editor.init}
            onClose={() => setEditor(null)}
            onDownload={downloadFromEditor}
          />
        )}
      </div>

      {/* Modal */}
      {modal && (
        <Modal
          p={modal.p}
          style={modal.style}
          onUse={() => openEditor(modal.p)}
          onDownload={() => downloadFromModal(modal.p)}
          onClose={() => setModal(null)}
        />
      )}
    </AppShell>
  )
}

/**
 * SEO card system — marketplace infographic quality.
 * Format: 540×720 (3:4 portrait). Export: 1080×1440 @2×.
 *
 * Design rules:
 * - Product fills full card canvas (object-fit: contain), AI bg shows at edges
 * - Radial vignette on every card — depth without flat black
 * - Text floats OVER the image; localized gradients ensure readability
 * - Max overlay opacity 0.45 anywhere except text panel (0.90 gradient)
 * - Adaptive font: long strings scale down, never overflow
 * - Series cohesion: accent top stripe on every slide
 */

import React from 'react'

export const CARD_W = 540
export const CARD_H = 720

const FONT_DEFAULT = 'Arial, Helvetica, sans-serif'

// ── Typography presets ────────────────────────────────────────────────────────

export interface TypographyConfig {
  accentColor:      string
  accentColorLight: string
  font:             string
  overlayStrength:  number
}

export const TYPOGRAPHY_PRESETS: Record<string, TypographyConfig & { label: string }> = {
  'wb-aggressive': {
    label:            'WB Агрессивный',
    accentColor:      '#22c55e',
    accentColorLight: 'rgba(34,197,94,0.15)',
    font:             'Arial, Helvetica, sans-serif',
    overlayStrength:  1.0,
  },
  'premium-minimal': {
    label:            'Premium Минимал',
    accentColor:      '#7C3AED',
    accentColorLight: 'rgba(124,58,237,0.15)',
    font:             'Georgia, "Times New Roman", serif',
    overlayStrength:  0.88,
  },
  'tech-clean': {
    label:            'Tech Чистый',
    accentColor:      '#60a5fa',
    accentColorLight: 'rgba(96,165,250,0.12)',
    font:             'Arial, Helvetica, sans-serif',
    overlayStrength:  0.93,
  },
  'beauty-soft': {
    label:            'Beauty Мягкий',
    accentColor:      '#f9a8d4',
    accentColorLight: 'rgba(249,168,212,0.12)',
    font:             'Georgia, "Times New Roman", serif',
    overlayStrength:  0.82,
  },
  'luxury-dark': {
    label:            'Luxury Тёмный',
    accentColor:      '#eab308',
    accentColorLight: 'rgba(234,179,8,0.15)',
    font:             'Georgia, "Times New Roman", serif',
    overlayStrength:  1.0,
  },
  'ozon-marketplace': {
    label:            'Ozon Маркет',
    accentColor:      '#005bff',
    accentColorLight: 'rgba(0,91,255,0.12)',
    font:             'Arial, Helvetica, sans-serif',
    overlayStrength:  0.93,
  },
}

export const DEFAULT_TYPOGRAPHY_PRESET = 'wb-aggressive'

// ── Card data ─────────────────────────────────────────────────────────────────

export interface CardData {
  background:    string
  productName:   string
  currentPrice:  string
  oldPrice:      string
  advantages:    string[]
  brandName?:    string
  productPhoto?: string | null
  typography?:   TypographyConfig
  marketplace?:  string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fitSize(text: string, base: number, threshold = 18): number {
  if (!text || text.length <= threshold) return base
  return Math.max(Math.round(base * 0.58), Math.round(base * threshold / text.length))
}

// Line-clamp helper — avoids repeating the awkward vendor props
function clamp(lines: number): React.CSSProperties {
  return {
    overflow: 'hidden',
    display: '-webkit-box',
    WebkitLineClamp: lines,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    WebkitBoxOrient: 'vertical' as any,
  }
}

function Check({ color, size = 10 }: { color: string; size?: number }) {
  return (
    <svg width={size} height={Math.round(size * 0.82)} viewBox="0 0 12 10" fill="none" style={{ display: 'block', flexShrink: 0 }}>
      <path d="M1 5L4.5 8.5L11 1.5" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

// ── Shared primitives ─────────────────────────────────────────────────────────

function AccentStripe({ color }: { color: string }) {
  return (
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0,
      height: 4, zIndex: 9, pointerEvents: 'none',
      background: `linear-gradient(90deg, ${color} 0%, ${color}66 70%, transparent 100%)`,
    }} />
  )
}

// ── CardBase ──────────────────────────────────────────────────────────────────

function CardBase({
  background, productPhoto, typography, children,
}: {
  background:    string
  productPhoto?: string | null
  typography?:   TypographyConfig
  children:      React.ReactNode
}) {
  const isGradient = /^(linear|radial|conic)-gradient/.test(background)

  return (
    <div style={{
      position:   'relative',
      width:      CARD_W,
      height:     CARD_H,
      overflow:   'hidden',
      flexShrink: 0,
      fontFamily: typography?.font ?? FONT_DEFAULT,
      background:      isGradient ? background : '#1a1a1e',
      backgroundImage: isGradient ? undefined : `url("${background}")`,
      backgroundSize:     'cover',
      backgroundPosition: 'center',
    }}>
      {/* Product image — fills full canvas, AI bg visible at edges */}
      {productPhoto && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 2,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <img
            src={productPhoto} alt=""
            style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
          />
        </div>
      )}

      {/* Vignette — edge depth, always on */}
      <div style={{
        position: 'absolute', inset: 0, zIndex: 3, pointerEvents: 'none',
        background: 'radial-gradient(ellipse at 50% 40%, transparent 25%, rgba(0,0,0,0.20) 62%, rgba(0,0,0,0.46) 100%)',
      }} />

      {children}
    </div>
  )
}

// ── Slide 1 — Main / Hero ─────────────────────────────────────────────────────
// Composition: product fills card · price top-right · title + benefits bottom

export function CardMain({ data }: { data: CardData }) {
  const { background, productName, currentPrice, oldPrice, advantages, productPhoto, typography } = data
  const accent = typography?.accentColor ?? '#22c55e'
  const advs   = advantages.filter(Boolean).concat(['Преимущество 1', 'Преимущество 2']).slice(0, 2)
  const nameFs = fitSize(productName || '', 48, 16)

  return (
    <CardBase background={background} productPhoto={productPhoto} typography={typography}>
      <AccentStripe color={accent} />

      {/* Bottom gradient panel — readable text zone */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: '46%',
        zIndex: 4, pointerEvents: 'none',
        background: 'linear-gradient(to top, rgba(0,0,0,0.90) 0%, rgba(0,0,0,0.62) 50%, transparent 100%)',
      }} />

      {/* Price badge — top-right floating sticker */}
      {currentPrice && (
        <div style={{
          position: 'absolute', top: 20, right: 20, zIndex: 8,
          background: accent, borderRadius: 10,
          padding: '8px 14px', textAlign: 'center',
          boxShadow: `0 2px 18px ${accent}66`,
        }}>
          <div style={{ fontSize: 24, fontWeight: 900, color: '#fff', lineHeight: 1, whiteSpace: 'nowrap' }}>
            {currentPrice} ₽
          </div>
          {oldPrice && (
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.62)', textDecoration: 'line-through', marginTop: 2 }}>
              {oldPrice} ₽
            </div>
          )}
        </div>
      )}

      {/* Bottom text */}
      <div style={{ position: 'absolute', bottom: 30, left: 28, right: 28, zIndex: 7 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <div style={{ width: 36, height: 3, background: accent, borderRadius: 2 }} />
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', color: accent, textTransform: 'uppercase' as const, opacity: 0.9 }}>
            Новинка
          </div>
        </div>

        <div style={{
          fontSize: nameFs, fontWeight: 900, color: '#fff',
          textTransform: 'uppercase' as const, lineHeight: 1.1,
          letterSpacing: '-0.01em',
          textShadow: '0 2px 12px rgba(0,0,0,0.85)',
          marginBottom: 18,
          ...clamp(3),
        }}>
          {productName || 'Название товара'}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 10 }}>
          {advs.map((adv, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{
                width: 18, height: 18, borderRadius: '50%', background: accent,
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              }}>
                <Check color="#fff" size={9} />
              </div>
              <span style={{
                fontSize: 14, fontWeight: 600, color: 'rgba(255,255,255,0.92)',
                textShadow: '0 1px 4px rgba(0,0,0,0.75)',
                overflow: 'hidden', whiteSpace: 'nowrap' as const, textOverflow: 'ellipsis',
              }}>
                {adv}
              </span>
            </div>
          ))}
        </div>
      </div>
    </CardBase>
  )
}

// ── Slide 2 — Benefit / Emotional statement ───────────────────────────────────
// Composition: product right · huge benefit headline left · cinematic feel

export function CardBenefit({ data }: { data: CardData }) {
  const { background, advantages, productPhoto, typography } = data
  const accent   = typography?.accentColor ?? '#22c55e'
  const headline = advantages[0] || 'Главное преимущество'
  const sub      = advantages[1] || 'Именно это делает товар лучшим выбором.'
  const headFs   = fitSize(headline, 62, 12)

  return (
    <CardBase background={background} productPhoto={productPhoto} typography={typography}>
      <AccentStripe color={accent} />

      {/* Left-side dark strip — text readability */}
      <div style={{
        position: 'absolute', inset: 0, zIndex: 4, pointerEvents: 'none',
        background: 'linear-gradient(to right, rgba(0,0,0,0.80) 0%, rgba(0,0,0,0.50) 46%, transparent 100%)',
      }} />

      {/* Vertical accent bar */}
      <div style={{
        position: 'absolute', top: 56, left: 0, bottom: 56, width: 4,
        zIndex: 8, pointerEvents: 'none',
        background: `linear-gradient(to bottom, ${accent} 0%, ${accent}44 100%)`,
      }} />

      {/* Text — left column */}
      <div style={{ position: 'absolute', top: 56, left: 26, width: 308, zIndex: 7 }}>
        <div style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.16em',
          color: accent, textTransform: 'uppercase' as const, marginBottom: 16, opacity: 0.9,
        }}>
          Ключевое преимущество
        </div>

        <div style={{
          fontSize: headFs, fontWeight: 900, color: '#fff',
          textTransform: 'uppercase' as const,
          lineHeight: 1.06, letterSpacing: '-0.02em',
          textShadow: '0 3px 18px rgba(0,0,0,0.80)',
          marginBottom: 20,
          ...clamp(4),
        }}>
          {headline}
        </div>

        <div style={{ width: 40, height: 3, background: accent, borderRadius: 2, marginBottom: 18 }} />

        <div style={{
          fontSize: 15, color: 'rgba(255,255,255,0.68)',
          lineHeight: 1.55,
          textShadow: '0 1px 6px rgba(0,0,0,0.65)',
          ...clamp(3),
        }}>
          {sub}
        </div>
      </div>
    </CardBase>
  )
}

// ── Slide 3 — Specs / Characteristics ────────────────────────────────────────
// Composition: product right · specs panel left · split layout

export function CardSpecs({ data }: { data: CardData }) {
  const { background, productName, advantages, productPhoto, typography } = data
  const accent = typography?.accentColor ?? '#60a5fa'
  const rows   = advantages.filter(Boolean).concat(['Характеристика 1', 'Характеристика 2', 'Характеристика 3']).slice(0, 3)
  const nameFs = fitSize(productName || '', 22, 24)

  return (
    <CardBase background={background} productPhoto={productPhoto} typography={typography}>
      <AccentStripe color={accent} />

      {/* Left panel darkening */}
      <div style={{
        position: 'absolute', inset: 0, zIndex: 4, pointerEvents: 'none',
        background: 'linear-gradient(to right, rgba(0,0,0,0.86) 0%, rgba(0,0,0,0.60) 44%, transparent 100%)',
      }} />

      {/* Left content */}
      <div style={{
        position: 'absolute', top: 48, left: 28, width: 260, bottom: 40, zIndex: 7,
        display: 'flex', flexDirection: 'column' as const,
      }}>
        <div style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.14em',
          color: accent, textTransform: 'uppercase' as const, marginBottom: 10, opacity: 0.9,
        }}>
          Характеристики
        </div>

        {productName && (
          <div style={{
            fontSize: nameFs, fontWeight: 700, color: 'rgba(255,255,255,0.88)',
            lineHeight: 1.2, marginBottom: 22,
            overflow: 'hidden', whiteSpace: 'nowrap' as const, textOverflow: 'ellipsis',
          }}>
            {productName}
          </div>
        )}

        <div style={{ borderTop: '1px solid rgba(255,255,255,0.12)', flex: 1 }}>
          {rows.map((val, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 12,
              padding: '16px 0',
              borderBottom: '1px solid rgba(255,255,255,0.08)',
            }}>
              <div style={{ width: 5, height: 5, borderRadius: '50%', background: accent, flexShrink: 0 }} />
              <div style={{
                fontSize: 17, fontWeight: 600, color: '#fff',
                textShadow: '0 1px 4px rgba(0,0,0,0.55)',
                overflow: 'hidden', whiteSpace: 'nowrap' as const, textOverflow: 'ellipsis',
              }}>
                {val}
              </div>
            </div>
          ))}
        </div>
      </div>
    </CardBase>
  )
}

// ── Slide 4 — Trust / Guarantee ───────────────────────────────────────────────
// Composition: product center · bottom heavy · "2 ГОДА" dominant · badges

export function CardTrust({ data }: { data: CardData }) {
  const { background, productPhoto, typography } = data
  const accent = typography?.accentColor ?? '#eab308'

  return (
    <CardBase background={background} productPhoto={productPhoto} typography={typography}>
      <AccentStripe color={accent} />

      {/* Bottom gradient */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: '52%',
        zIndex: 4, pointerEvents: 'none',
        background: 'linear-gradient(to top, rgba(0,0,0,0.92) 0%, rgba(0,0,0,0.62) 55%, transparent 100%)',
      }} />

      {/* Bottom centered */}
      <div style={{
        position: 'absolute', bottom: 32, left: 28, right: 28, zIndex: 7, textAlign: 'center' as const,
      }}>
        <div style={{
          width: 50, height: 50, borderRadius: 14, margin: '0 auto 16px',
          background: `${accent}22`, border: `2px solid ${accent}55`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <svg width="24" height="27" viewBox="0 0 32 36" fill="none">
            <path d="M16 2L4 8V17C4 24.73 9.33 31.93 16 34C22.67 31.93 28 24.73 28 17V8L16 2Z"
              stroke={accent} strokeWidth="2.5" strokeLinejoin="round" />
            <path d="M11 18L14.5 21.5L21 15"
              stroke={accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        <div style={{
          fontSize: 54, fontWeight: 900, color: '#fff', lineHeight: 1,
          textShadow: '0 3px 14px rgba(0,0,0,0.75)', marginBottom: 4,
        }}>
          2 ГОДА
        </div>
        <div style={{
          fontSize: 22, fontWeight: 700, color: accent,
          letterSpacing: '0.08em', textTransform: 'uppercase' as const, marginBottom: 22,
        }}>
          ГАРАНТИИ
        </div>

        <div style={{ display: 'flex', justifyContent: 'center' as const, gap: 8, flexWrap: 'wrap' as const }}>
          {['Сертифицировано', 'Проверено', 'Оригинал'].map(label => (
            <div key={label} style={{
              display: 'flex', alignItems: 'center', gap: 7,
              background: 'rgba(255,255,255,0.09)', border: '1px solid rgba(255,255,255,0.14)',
              borderRadius: 8, padding: '7px 12px',
            }}>
              <div style={{ width: 5, height: 5, borderRadius: '50%', background: accent, flexShrink: 0 }} />
              <span style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.85)', whiteSpace: 'nowrap' as const }}>
                {label}
              </span>
            </div>
          ))}
        </div>
      </div>
    </CardBase>
  )
}

// ── Slide 5 — Action / CTA ────────────────────────────────────────────────────
// Reference quality. Focal: CTA button. Dramatic, conversion-first.

export function CardAction({ data }: { data: CardData }) {
  const { background, productPhoto, typography, marketplace, currentPrice } = data
  const accent  = typography?.accentColor ?? '#005bff'
  const ctaText = marketplace === 'wb'   ? 'Заказать на Wildberries'
                : marketplace === 'ozon' ? 'Заказать на Ozon'
                : 'Оформить заказ'
  const ctaFs   = fitSize(ctaText, 18, 22)

  return (
    <CardBase background={background} productPhoto={productPhoto} typography={typography}>
      <AccentStripe color={accent} />

      {/* Heavy bottom gradient — CTA zone */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: '58%',
        zIndex: 4, pointerEvents: 'none',
        background: 'linear-gradient(to top, rgba(0,0,0,0.95) 0%, rgba(0,0,0,0.72) 42%, transparent 100%)',
      }} />

      {/* Bottom centered */}
      <div style={{
        position: 'absolute', bottom: 36, left: 28, right: 28, zIndex: 7,
        textAlign: 'center' as const,
      }}>
        <div style={{
          fontSize: 11, fontWeight: 700, letterSpacing: '0.18em',
          color: 'rgba(255,255,255,0.36)', textTransform: 'uppercase' as const, marginBottom: 10,
        }}>
          Не откладывай
        </div>

        <div style={{
          fontSize: 54, fontWeight: 900, color: '#fff',
          textTransform: 'uppercase' as const, letterSpacing: '0.01em',
          lineHeight: 1.06, textShadow: '0 3px 14px rgba(0,0,0,0.85)',
          marginBottom: 26,
        }}>
          ГОТОВЫ<br />К ЗАКАЗУ?
        </div>

        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: 10,
          background: accent, color: '#fff',
          fontSize: ctaFs, fontWeight: 700,
          padding: '16px 38px', borderRadius: 12,
          boxShadow: `0 0 30px ${accent}77, 0 4px 20px rgba(0,0,0,0.45)`,
          letterSpacing: '0.01em',
        }}>
          {ctaText}
          <span style={{ fontSize: 20, lineHeight: 1 }}>→</span>
        </div>

        {currentPrice && (
          <div style={{ marginTop: 14, fontSize: 22, fontWeight: 900, color: accent }}>
            {currentPrice} ₽
          </div>
        )}

        <div style={{ marginTop: 8, fontSize: 11, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.05em' }}>
          Быстрая доставка · Гарантия качества
        </div>
      </div>
    </CardBase>
  )
}

// ── Slide 6 — Final / Premium showcase ───────────────────────────────────────
// Strongest composition. Product dominant. Closing brand statement.

export function CardFinal({ data }: { data: CardData }) {
  const { background, productName, brandName, productPhoto, typography, currentPrice } = data
  const accent = typography?.accentColor ?? '#ffffff'
  const nameFs = fitSize(productName || '', 52, 12)

  return (
    <CardBase background={background} productPhoto={productPhoto} typography={typography}>
      {/* Bottom gradient */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: '40%',
        zIndex: 4, pointerEvents: 'none',
        background: 'linear-gradient(to top, rgba(0,0,0,0.90) 0%, rgba(0,0,0,0.52) 55%, transparent 100%)',
      }} />

      {/* Bottom accent stripe */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0, height: 4,
        zIndex: 9, pointerEvents: 'none',
        background: `linear-gradient(90deg, ${accent} 0%, ${accent}44 70%, transparent 100%)`,
      }} />

      {/* Brand — top-left */}
      <div style={{ position: 'absolute', top: 24, left: 28, zIndex: 7 }}>
        <div style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.22em',
          color: 'rgba(255,255,255,0.34)', textTransform: 'uppercase' as const,
        }}>
          {brandName || 'Ваш бренд'}
        </div>
      </div>

      {/* Bottom content */}
      <div style={{ position: 'absolute', bottom: 24, left: 28, right: 28, zIndex: 7 }}>
        <div style={{ width: 40, height: 3, background: accent, borderRadius: 2, marginBottom: 14 }} />

        <div style={{
          fontSize: nameFs, fontWeight: 900, color: '#fff',
          textTransform: 'uppercase' as const,
          lineHeight: 1.06, letterSpacing: '-0.02em',
          textShadow: '0 3px 18px rgba(0,0,0,0.75)',
          marginBottom: 14,
          ...clamp(2),
        }}>
          {productName || 'Ваш товар'}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' as const, gap: 10 }}>
          <div style={{
            fontSize: 11, fontWeight: 700,
            color: 'rgba(255,255,255,0.42)',
            letterSpacing: '0.07em', textTransform: 'uppercase' as const,
          }}>
            Качество · Надёжность
          </div>
          {currentPrice && (
            <div style={{ fontSize: 20, fontWeight: 900, color: accent, flexShrink: 0 }}>
              {currentPrice} ₽
            </div>
          )}
        </div>
      </div>
    </CardBase>
  )
}

// ── Registry ──────────────────────────────────────────────────────────────────

export const CARD_COMPONENTS: Record<string, React.ComponentType<{ data: CardData }>> = {
  main:    CardMain,
  benefit: CardBenefit,
  specs:   CardSpecs,
  trust:   CardTrust,
  action:  CardAction,
  final:   CardFinal,
}

export const CARD_LABELS: Record<string, string> = {
  main:    'Главный слайд',
  benefit: 'Преимущество',
  specs:   'Характеристики',
  trust:   'Гарантия',
  action:  'Призыв к действию',
  final:   'Финальный',
}

export const CARD_ORDER = ['main', 'benefit', 'specs', 'trust', 'action', 'final'] as const

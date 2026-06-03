/**
 * PULT Design System — single source of truth for visual tokens.
 * Import as `import { T } from '@/lib/tokens'` in any dashboard component.
 */

export const T = {
  // ── Backgrounds ──────────────────────────────────────────────────────────────
  bg:     '#09090B',   // outer shell (layout, sidebar)
  bgPage: '#1C1C1E',   // inner page content areas
  surf:   '#111113',   // card / widget surface
  surfH:  '#18181B',   // hover surface

  // ── Borders ──────────────────────────────────────────────────────────────────
  line:     'rgba(255,255,255,0.08)',  // card / component borders (subtle)
  lineHard: '#232329',                // structural borders (sidebar, dividers)

  // ── Text ─────────────────────────────────────────────────────────────────────
  text:  '#EDEDF0',   // primary
  text2: '#8E8E93',   // secondary
  text3: '#6B6B72',   // tertiary / metadata

  // ── Accent — violet ──────────────────────────────────────────────────────────
  v:     '#6E6AFC',
  vMid:  '#A78BFA',                    // lighter violet for text on dark
  vDim:  'rgba(110,106,252,0.12)',     // tinted bg (active state, hover)
  vHint: 'rgba(110,106,252,0.07)',     // very subtle hint

  // ── Status ───────────────────────────────────────────────────────────────────
  ok:    '#22C55E',
  okD:   'rgba(34,197,94,0.10)',
  warn:  '#F59E0B',
  warnD: 'rgba(245,158,11,0.10)',
  red:   '#EF4444',
  redD:  'rgba(239,68,68,0.10)',

  // ── Typography scale (px) ────────────────────────────────────────────────────
  sz: {
    pageTitle: 20,   // page h1
    heading:   15,   // card heading, modal title
    body:      13,   // standard body copy
    label:     10,   // section labels — always uppercase + tracking
    caption:   11,   // captions, timestamps, metadata
    micro:     9.5,  // badges, chips
  },

  // ── Radius ───────────────────────────────────────────────────────────────────
  r: {
    card:  9,
    btn:   6,
    badge: 4,
    pill:  20,
  },

  // ── Page layout ──────────────────────────────────────────────────────────────
  layout: {
    padding:     '28px 32px 64px' as const,
    paddingMobile: '20px 16px 64px' as const,
    maxWidth:    960,
    sectionGap:  24,  // vertical gap between major page sections
    cardPad:     '14px 16px' as const,
  },
} as const

import { IBM_Plex_Mono, IBM_Plex_Sans, Source_Serif_4 } from 'next/font/google'

// Fonts for the Executive Ledger screens ONLY. Declared here and applied by the three route
// layouts that own those screens, never in app/layout.tsx — loading them globally would change
// the typography of every other page in PULT, which this slice must not touch.

export const ledgerSerif = Source_Serif_4({
  subsets: ['latin', 'cyrillic'],
  variable: '--font-ledger-serif',
  display: 'swap',
  weight: ['400', '600'],
})

export const ledgerSans = IBM_Plex_Sans({
  subsets: ['latin', 'cyrillic'],
  variable: '--font-ledger-sans',
  display: 'swap',
  weight: ['400', '500', '600'],
})

export const ledgerMono = IBM_Plex_Mono({
  subsets: ['latin'],
  variable: '--font-ledger-mono',
  display: 'swap',
  weight: ['400', '500'],
})

export const ledgerFontVars = `${ledgerSerif.variable} ${ledgerSans.variable} ${ledgerMono.variable}`

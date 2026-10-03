import localFont from 'next/font/local'

// Fonts for the Executive Ledger screens ONLY. Declared here and applied by the three route
// layouts that own those screens, never in app/layout.tsx — loading them globally would change
// the typography of every other page in PULT, which this slice must not touch.

export const ledgerSerif = localFont({
  src: '../../fonts/sourceserif4/SourceSerif4[opsz,wght].woff2',
  variable: '--font-ledger-serif',
  display: 'swap',
  weight: '400 600',
  style: 'normal',
  adjustFontFallback: 'Times New Roman',
})

export const ledgerSans = localFont({
  src: '../../fonts/ibmplexsans/IBMPlexSans[wdth,wght].ttf',
  variable: '--font-ledger-sans',
  display: 'swap',
  weight: '400 600',
  style: 'normal',
})

export const ledgerMono = localFont({
  src: [
    { path: '../../fonts/ibmplexmono/IBMPlexMono-Regular.ttf', weight: '400', style: 'normal' },
    { path: '../../fonts/ibmplexmono/IBMPlexMono-Medium.ttf', weight: '500', style: 'normal' },
  ],
  variable: '--font-ledger-mono',
  display: 'swap',
})

export const ledgerFontVars = `${ledgerSerif.variable} ${ledgerSans.variable} ${ledgerMono.variable}`

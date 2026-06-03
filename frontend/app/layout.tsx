import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import '../styles/globals.css'
import { LangProvider } from '@/lib/lang-context'
import { RippleProvider } from '@/components/RippleProvider'
import { CookieBanner } from '@/components/CookieBanner'

const inter = Inter({
  subsets: ['latin', 'cyrillic'],
  variable: '--font-inter',
  display: 'swap',
  weight: ['400', '500', '600', '700', '800'],
})

const mono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
  weight: ['400', '500', '600'],
})

export const metadata: Metadata = {
  title: 'ПУЛЬТ — Центр управления бизнесом на маркетплейсах',
  description: 'Операционная система для селлеров WB, Ozon и Яндекс Маркет. Мониторинг, аналитика, автоматизация.',
  icons: {
    icon: '/favicon.svg',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen antialiased">
        <LangProvider>
          <RippleProvider />
          {children}
          <CookieBanner />
        </LangProvider>
      </body>
    </html>
  )
}

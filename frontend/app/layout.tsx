import type { Metadata } from 'next'
import localFont from 'next/font/local'
import '../styles/globals.css'
import '../styles/seller.css'
import { LangProvider } from '@/lib/lang-context'
import { RippleProvider } from '@/components/RippleProvider'
import { CookieBanner } from '@/components/CookieBanner'

const inter = localFont({
  src: '../fonts/inter/Inter[opsz,wght].woff2',
  variable: '--font-inter',
  display: 'swap',
  weight: '400 800',
  style: 'normal',
})

const mono = localFont({
  src: '../fonts/jetbrainsmono/JetBrainsMono[wght].woff2',
  variable: '--font-mono',
  display: 'swap',
  weight: '400 600',
  style: 'normal',
})

export const metadata: Metadata = {
  title: 'Пульт — операционная система селлера',
  description: 'Спрос, продажи, маржа, остатки и ежедневные решения для продавцов на маркетплейсах — в едином операционном контуре.',
  icons: {
    icon: '/favicon.svg',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${inter.variable} ${mono.variable}`} suppressHydrationWarning>
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

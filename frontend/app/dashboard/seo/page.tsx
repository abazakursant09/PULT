import Link from 'next/link'
import { FileText, FlaskConical, TrendingUp } from 'lucide-react'

// L3 — Execution Layer: SEO tool. Single home for all SEO surfaces (one entity = one home).
// Consolidates the formerly separate seo-cards / seo-lab / seo-intelligence routes.

const BG = '#1C1C1E'
const CARD = '#232325'
const BORDER = 'rgba(255,255,255,0.07)'
const VIOLET = '#C4B5FD'
const MUTED = '#8A8A93'

const ITEMS = [
  { href: '/dashboard/seo-cards',        icon: FileText,     title: 'Карточки',     desc: 'Сборка и авто-пересборка карточек' },
  { href: '/dashboard/seo-lab',          icon: FlaskConical, title: 'Lab',          desc: 'WB vs Ozon, проверка стиля карточки' },
  { href: '/dashboard/seo-intelligence', icon: TrendingUp,   title: 'Intelligence', desc: 'Лидерборд стилей и результатов' },
]

export default function SeoHubPage() {
  return (
    <div style={{ background: BG, minHeight: '100vh', padding: '28px 24px', maxWidth: 1080, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, color: '#FFF', marginBottom: 4 }}>SEO</h1>
      <p style={{ fontSize: 13, color: MUTED, marginBottom: 24 }}>Инструменты влияния на продажи через карточку.</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
        {ITEMS.map(({ href, icon: Icon, title, desc }) => (
          <Link key={href} href={href}
            style={{ display: 'block', textDecoration: 'none', background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: 18 }}>
            <Icon size={18} color={VIOLET} />
            <div style={{ fontSize: 15, fontWeight: 700, color: '#EDEDED', margin: '10px 0 4px' }}>{title}</div>
            <div style={{ fontSize: 12.5, color: MUTED }}>{desc}</div>
          </Link>
        ))}
      </div>
    </div>
  )
}

import Link from 'next/link'
import { BarChart2, Upload, Calculator, Target, Users, FileText } from 'lucide-react'

// L2 — Truth Layer hub. Single home for facts. No recommendations, no "what to do" CTAs.
// Pure navigation launcher into existing factual screens (one entity = one home).

const BG = '#1C1C1E'
const CARD = '#232325'
const BORDER = 'rgba(255,255,255,0.07)'
const VIOLET = '#C4B5FD'
const MUTED = '#8A8A93'

const ITEMS = [
  { href: '/dashboard/finance', icon: BarChart2,  title: 'Финансы',    desc: 'Выручка, прибыль и маржа по товарам' },
  { href: '/dashboard/import',  icon: Upload,     title: 'Товары и импорт', desc: 'Загрузка и список товаров' },
  { href: '/profit-calculator', icon: Calculator, title: 'Цены',       desc: 'Юнит-экономика и расчёт цены' },
  { href: '/market-overview',   icon: Target,     title: 'Конкуренты', desc: 'Сравнение с рынком' },
  { href: '/dashboard/deals',   icon: Users,      title: 'Сделки',     desc: 'История и статусы сделок' },
  { href: '/dashboard/marking', icon: FileText,   title: 'Маркировка', desc: 'Маркировка товаров' },
]

export default function DataHubPage() {
  return (
    <div style={{ background: BG, minHeight: '100vh', padding: '28px 24px', maxWidth: 1080, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, color: '#FFF', marginBottom: 4 }}>Данные</h1>
      <p style={{ fontSize: 13, color: MUTED, marginBottom: 24 }}>Факты вашего бизнеса. Без рекомендаций — за решениями в «Пульт».</p>
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

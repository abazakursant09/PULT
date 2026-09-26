import Link from 'next/link'
import { Upload, Calculator, Store, Truck, FileText } from 'lucide-react'

// L2 — Truth Layer hub. Single home for facts. No recommendations, no "what to do" CTAs.
// Pure navigation launcher into existing factual screens (one entity = one home).

const BG = 'var(--bg)'
const CARD = 'var(--surface)'
const BORDER = 'var(--line)'
const VIOLET = 'var(--violet-text)'
const MUTED = 'var(--text-2)'

const ITEMS = [
  { href: '/dashboard/stores',  icon: Store,      title: 'Магазины',   desc: 'Источники и данные каждого магазина' },
  { href: '/dashboard/import',  icon: Upload,     title: 'Товары и импорт', desc: 'Загрузка и список товаров' },
  { href: '/profit-calculator', icon: Calculator, title: 'Цены',       desc: 'Юнит-экономика и расчёт цены' },
  { href: '/logistics',         icon: Truck,      title: 'Логистика',  desc: 'Расчёт и сравнение вариантов доставки' },
  { href: '/dashboard/marking', icon: FileText,   title: 'Маркировка', desc: 'Маркировка товаров' },
]

export default function DataHubPage() {
  return (
    <div style={{ background: BG, minHeight: '100vh', padding: '28px 24px', maxWidth: 1080, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, color: 'var(--text)', marginBottom: 4 }}>Данные</h1>
      <p style={{ fontSize: 13, color: MUTED, marginBottom: 24 }}>Факты вашего бизнеса. Без рекомендаций — за решениями в «Пульт».</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(280px, 100%), 1fr))', gap: 12 }}>
        {ITEMS.map(({ href, icon: Icon, title, desc }) => (
          <Link key={href} href={href}
            style={{ display: 'block', textDecoration: 'none', background: CARD, border: `1px solid ${BORDER}`, borderRadius: 12, padding: 18 }}>
            <Icon size={18} color={VIOLET} />
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', margin: '10px 0 4px' }}>{title}</div>
            <div style={{ fontSize: 12.5, color: MUTED }}>{desc}</div>
          </Link>
        ))}
      </div>
    </div>
  )
}

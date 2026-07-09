'use client'
import Link from 'next/link'

// P7.2 — honest placeholder. Replaces surfaces that are not yet backed by real
// tenant data (the former mock Seller Cabinet, dead SEO hub, and unfinished
// features). Shows NO data — only an honest "in development" state — so the launch
// product never presents fabricated numbers.
export function ComingSoon({ title = 'Раздел в разработке' }: { title?: string }) {
  return (
    <div style={{
      minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '40px 20px',
    }}>
      <div style={{ maxWidth: 420, textAlign: 'center' }}>
        <div style={{
          width: 56, height: 56, borderRadius: 14, margin: '0 auto 18px',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'var(--surface-h, rgba(110,106,252,0.08))',
          border: '1px solid var(--line, rgba(110,106,252,0.18))',
        }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="1.8" style={{ color: 'var(--text-3, #8A8A8A)' }}>
            <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
          </svg>
        </div>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text, #E5E7EB)', margin: '0 0 8px' }}>
          {title}
        </h2>
        <p style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-2, #9AA1AD)', margin: '0 0 18px' }}>
          Этот раздел ещё готовится. Пока PULT работает как советник по бизнесу:
          загрузите выгрузки маркетплейса — и получите диагноз, что чинить и почему.
        </p>
        <Link href="/dashboard" className="btn btn-primary"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          Перейти в Пульт
        </Link>
      </div>
    </div>
  )
}

export default ComingSoon

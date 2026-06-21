'use client'

// Contour filters. No numeric priority, no sorting controls — just which contour.
export const FEED_CONTOURS: { v: string | null; l: string }[] = [
  { v: null, l: 'Все' },
  { v: 'seo', l: 'SEO' },
  { v: 'advertising', l: 'Реклама' },
  { v: 'review', l: 'Отзывы' },
  { v: 'growth', l: 'Рост' },
  { v: 'legal', l: 'Юридические риски' },
  { v: 'decision_outcome', l: 'Эффект решений' },
]

export function DecisionFeedFilters(
  { value, onChange }: { value: string | null; onChange: (v: string | null) => void },
) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
      {FEED_CONTOURS.map((c) => {
        const on = value === c.v
        return (
          <button key={c.l} onClick={() => onChange(c.v)} style={{
            fontSize: 12, padding: '5px 11px', borderRadius: 7, cursor: 'pointer',
            border: '1px solid var(--line)',
            background: on ? 'var(--surface-h)' : 'transparent',
            color: on ? 'var(--text)' : 'var(--text-3)',
          }}>{c.l}</button>
        )
      })}
    </div>
  )
}

export default DecisionFeedFilters

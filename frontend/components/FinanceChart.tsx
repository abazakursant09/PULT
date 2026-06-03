interface Segment {
  label: string
  value: number
  color: string
}

interface Props {
  segments: Segment[]
  size?: number
  holeRatio?: number
}

export function FinanceChart({ segments, size = 168, holeRatio = 0.62 }: Props) {
  const total = segments.reduce((s, seg) => s + Math.max(seg.value, 0), 0)
  if (total === 0) return null

  let deg = 0
  const stops = segments
    .filter(s => s.value > 0)
    .map(seg => {
      const start = deg
      deg += (seg.value / total) * 360
      return `${seg.color} ${start.toFixed(1)}deg ${deg.toFixed(1)}deg`
    })
    .join(', ')

  const holeSize = Math.round(size * holeRatio)
  const holePx   = `${holeSize}px`
  const holeBg   = '#E8E5E0'

  return (
    <div className="flex flex-col sm:flex-row items-center gap-5 sm:gap-8">
      <div
        className="shrink-0"
        style={{
          width:  size,
          height: size,
          borderRadius: '50%',
          background: `radial-gradient(closest-side, ${holeBg} ${Math.round(holeRatio * 100)}%, transparent ${Math.round(holeRatio * 100)}%), conic-gradient(${stops})`,
        }}
      />

      <ul className="flex flex-col gap-2 min-w-0">
        {segments.filter(s => s.value > 0).map(seg => (
          <li key={seg.label} className="flex items-center gap-2 text-xs">
            <span className="shrink-0 w-2.5 h-2.5 rounded-sm" style={{ background: seg.color }} />
            <span className="truncate" style={{ color: '#8A8986' }}>{seg.label}</span>
            <span className="ml-auto font-semibold tabular-nums pl-4 shrink-0" style={{ color: '#202124' }}>
              {total > 0 ? `${((seg.value / total) * 100).toFixed(1)} %` : '—'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

'use client'
import { T } from '@/lib/tokens'

interface SkeletonListProps {
  count?:  number
  height?: number
  gap?:    number
}

export function SkeletonList({ count = 3, height = 72, gap = 10 }: SkeletonListProps) {
  return (
    <>
      <style>{`
        @keyframes pult-pulse {
          0%, 100% { opacity: 0.6; }
          50%       { opacity: 0.3; }
        }
      `}</style>
      <div style={{ display: 'flex', flexDirection: 'column', gap }}>
        {Array.from({ length: count }, (_, i) => (
          <div
            key={i}
            style={{
              height,
              borderRadius: T.r.card,
              background:   T.surf,
              border:       `1px solid ${T.line}`,
              animation:    'pult-pulse 1.5s ease-in-out infinite',
              animationDelay: `${i * 120}ms`,
            }}
          />
        ))}
      </div>
    </>
  )
}

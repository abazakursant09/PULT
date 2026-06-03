'use client'
import * as React from 'react'
import { cn } from '@/lib/utils'

interface BorderBeamProps {
  className?: string
  size?: number
  duration?: number
  colorFrom?: string
  colorTo?: string
  delay?: number
}

export function BorderBeam({ className, size = 200, duration = 15, colorFrom = '#1A73E8', colorTo = '#34D399', delay = 0 }: BorderBeamProps) {
  return (
    <div
      className={cn('pointer-events-none absolute inset-0 rounded-[inherit]', className)}
      style={
        {
          '--size': size,
          '--duration': duration,
          '--color-from': colorFrom,
          '--color-to': colorTo,
          '--delay': `-${delay}s`,
        } as React.CSSProperties
      }
    >
      <div
        className="absolute inset-[0] rounded-[inherit] [border:calc(var(--size)*0.01px)_solid_transparent]"
        style={{
          background: `linear-gradient(hsl(var(--background)), hsl(var(--background))) padding-box, conic-gradient(from calc(var(--delay)) at 50% 50%, transparent 0%, var(--color-from) 30%, var(--color-to) 60%, transparent 100%) border-box`,
          animation: `borderBeam calc(var(--duration)*1s) linear infinite`,
        }}
      />
    </div>
  )
}

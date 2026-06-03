'use client'
import * as React from 'react'
import { cn } from '@/lib/utils'

interface MagicCardProps extends React.HTMLAttributes<HTMLDivElement> {
  gradientColor?: string
  gradientSize?: number
}

export function MagicCard({ children, className, gradientColor = 'rgba(26,115,232,0.08)', gradientSize = 250, ...props }: MagicCardProps) {
  const ref = React.useRef<HTMLDivElement>(null)
  const [pos, setPos] = React.useState({ x: -9999, y: -9999 })

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    setPos({ x: e.clientX - rect.left, y: e.clientY - rect.top })
  }

  const handleMouseLeave = () => setPos({ x: -9999, y: -9999 })

  return (
    <div
      ref={ref}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      className={cn('relative overflow-hidden rounded-xl border border-border bg-background', className)}
      style={{
        background: `radial-gradient(${gradientSize}px circle at ${pos.x}px ${pos.y}px, ${gradientColor}, transparent 80%), hsl(var(--background))`,
      }}
      {...props}
    >
      {children}
    </div>
  )
}

'use client'
import * as React from 'react'
import { cn } from '@/lib/utils'

interface ShimmerButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  shimmerColor?: string
  background?: string
  shimmerDuration?: string
}

export function ShimmerButton({
  children,
  className,
  shimmerColor = 'rgba(255,255,255,0.3)',
  background = '#1A73E8',
  shimmerDuration = '2s',
  ...props
}: ShimmerButtonProps) {
  return (
    <button
      className={cn(
        'relative inline-flex items-center justify-center overflow-hidden rounded-lg px-6 py-3',
        'text-sm font-semibold text-white transition-all duration-200',
        'hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        'disabled:pointer-events-none disabled:opacity-50',
        className,
      )}
      style={{ background }}
      {...props}
    >
      <span
        className="absolute inset-0 -translate-x-full"
        style={{
          background: `linear-gradient(90deg, transparent, ${shimmerColor}, transparent)`,
          animation: `shimmerMove ${shimmerDuration} infinite`,
        }}
      />
      <span className="relative z-10">{children}</span>
    </button>
  )
}

import * as React from 'react'
import { cn } from '@/lib/utils'

// Canonical PULT skeleton (P1). Calm shimmer, not a flashing pulse. The visual lives in the
// `.pult-skeleton` utility (globals.css) so it stays on P0 tokens and honours reduced-motion.
// No layout shift: the caller reserves the box via width/height/className.
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn('pult-skeleton', className)}
      {...props}
    />
  )
}

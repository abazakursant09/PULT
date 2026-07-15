import * as React from 'react'
import { cn } from '@/lib/utils'

// Canonical PULT badge (P1). Semantic variants on P0 tokens — success / warning / danger / neutral.
// Old shadcn names (default / secondary / destructive / outline) stay as aliases so existing pages
// keep working. Tinted fills use the *-dim tokens; no raw Tailwind colour literals, no raw hex.
type Variant =
  | 'success' | 'warning' | 'danger' | 'neutral'
  | 'default' | 'secondary' | 'destructive' | 'outline'   // back-compat aliases

const _success = 'bg-[var(--success-dim)] text-[var(--success)] border-transparent'
const _warning = 'bg-[var(--warning-dim)] text-[var(--warning)] border-transparent'
const _danger  = 'bg-[var(--danger-dim)] text-[var(--danger)] border-transparent'
const _neutral = 'bg-[var(--surface-h)] text-[var(--text-2)] border-transparent'

const variants: Record<Variant, string> = {
  success: _success,
  warning: _warning,
  danger:  _danger,
  neutral: _neutral,
  // aliases
  default:     'bg-[var(--violet-dim)] text-[var(--violet-text)] border-transparent',
  secondary:   _neutral,
  destructive: _danger,
  outline:     'bg-transparent text-[var(--text)] border-[var(--line)]',
}

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: Variant
}

export function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5',
        'text-xs font-semibold transition-colors duration-[var(--dur)]',
        variants[variant],
        className,
      )}
      {...props}
    />
  )
}

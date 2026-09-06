import * as React from 'react'
import { cn } from '@/lib/utils'

// Canonical PULT button (P1). Semantic variants primary / secondary / ghost / danger. The old
// shadcn names (default / destructive / outline / link) stay as aliases so existing pages keep
// working without a page-level change. All colours resolve to P0 tokens — no raw hex. Motion:
// a subtle scale on :active for press feedback, transitions only on transform/colour (emil).
type Variant =
  | 'primary' | 'secondary' | 'ghost' | 'danger'
  | 'default' | 'destructive' | 'outline' | 'link'   // back-compat aliases
type Size = 'default' | 'sm' | 'lg' | 'icon'

const _primary   = 'bg-[var(--violet)] text-[hsl(var(--primary-foreground))] font-semibold hover:bg-[var(--violet-h)]'
const _danger    = 'bg-[var(--danger)] text-white font-semibold hover:opacity-90'
const _secondary = 'bg-transparent border border-[var(--line)] text-[var(--text)] hover:border-[var(--violet-text)] hover:text-[var(--violet-text)]'
const _ghost     = 'bg-transparent text-[var(--text-2)] hover:bg-[var(--surface-h)] hover:text-[var(--text)]'

const variants: Record<Variant, string> = {
  primary:     _primary,
  secondary:   _secondary,
  ghost:       _ghost,
  danger:      _danger,
  // aliases
  default:     _primary,
  destructive: _danger,
  outline:     _secondary,
  link:        'bg-transparent text-[var(--violet-text)] hover:text-[var(--violet-h)] underline-offset-4 hover:underline',
}

const sizes: Record<Size, string> = {
  default: 'h-[44px] px-6 text-[15px]',
  sm:      'h-8 px-4 text-[13px] rounded-[var(--r-sm)]',
  lg:      'h-[44px] px-8 text-[15px]',
  icon:    'h-9 w-9',
}

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'default', loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'pult-btn inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--r-sm)]',
        // press feedback + colour transitions only (no `all`), fast, strong ease-out
        'transition-[transform,background-color,border-color,color] duration-150 [transition-timing-function:var(--ease-out)]',
        'active:scale-[0.97]',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--violet-text)]',
        'disabled:pointer-events-none disabled:opacity-40 disabled:active:scale-100',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading && (
        <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24" aria-hidden>
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  )
)
Button.displayName = 'Button'

import * as React from 'react'
import { cn } from '@/lib/utils'

// Canonical PULT input (P1). All colours on P0 tokens — no raw hex. States: default, focus (violet
// ring), error (danger border, set `aria-invalid`), disabled (dimmed, not-allowed). Motion limited
// to border/colour transitions.
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Renders the error state (danger border) and sets aria-invalid for assistive tech. */
  error?: boolean
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, error, 'aria-invalid': ariaInvalid, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      aria-invalid={error || ariaInvalid}
      className={cn(
        'flex h-[44px] w-full rounded-[var(--r-sm)]',
        'border border-[var(--line)] bg-[var(--bg)]',
        'px-3 text-[15px] text-[var(--text)]',
        'placeholder:text-[var(--text-3)]',
        'transition-[border-color,box-shadow] duration-[var(--dur)] [transition-timing-function:var(--ease)]',
        'focus-visible:outline-none focus-visible:border-[var(--violet-text)]',
        'disabled:cursor-not-allowed disabled:opacity-40',
        // error state wins over the default border (works via aria-invalid or the `error` prop)
        'aria-[invalid=true]:border-[var(--danger)] aria-[invalid=true]:focus-visible:border-[var(--danger)]',
        className,
      )}
      {...props}
    />
  )
)
Input.displayName = 'Input'

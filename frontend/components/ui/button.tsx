import * as React from 'react'
import { cn } from '@/lib/utils'

type Variant = 'default' | 'destructive' | 'outline' | 'secondary' | 'ghost' | 'link'
type Size    = 'default' | 'sm' | 'lg' | 'icon'

/* Only 2 real visual types:
   primary  = gold fill  (default, destructive mapped separately)
   secondary = transparent + border (#1A1A1A) */
const variants: Record<Variant, string> = {
  default:     'bg-[#7C3AED] text-white font-semibold hover:bg-[#8B5CF6]',
  destructive: 'bg-[#EF4444] text-white font-semibold hover:bg-[#DC2626]',
  outline:     'bg-transparent border border-[#1A1A1A] text-white hover:border-[#A78BFA] hover:text-[#A78BFA]',
  secondary:   'bg-transparent border border-[#1A1A1A] text-white hover:border-[#A78BFA] hover:text-[#A78BFA]',
  ghost:       'bg-transparent border border-[#1A1A1A] text-white hover:border-[#A78BFA] hover:text-[#A78BFA]',
  link:        'bg-transparent text-[#A78BFA] hover:text-[#8B5CF6] underline-offset-4 hover:underline',
}

const sizes: Record<Size, string> = {
  default: 'h-[44px] px-6 text-[15px]',
  sm:      'h-8 px-4 text-[13px] rounded-[8px]',
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
  ({ className, variant = 'default', size = 'default', loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[8px]',
        'transition-all duration-200',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#A78BFA]',
        'disabled:pointer-events-none disabled:opacity-40',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading && (
        <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  )
)
Button.displayName = 'Button'

import * as React from 'react'
import { cn } from '@/lib/utils'

// Canonical PULT card (P1). Three surface levels on P0 tokens:
//   surface  — default card on the app background
//   elevated — a raised surface (popover / floating panel)
//   bordered — flat, no fill, hairline only (list rows, quiet groupings)
// All colours are tokens; no raw hex.
type CardVariant = 'surface' | 'elevated' | 'bordered'

const cardVariants: Record<CardVariant, string> = {
  surface:  'border border-[var(--line)] bg-[var(--surface)]',
  elevated: 'border border-[var(--line)] bg-[var(--elevated)]',
  bordered: 'border border-[var(--line)] bg-transparent',
}

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant
}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = 'surface', ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'rounded-[var(--r-sm)] text-[var(--text)]',
        'transition-[background-color,border-color] duration-200 [transition-timing-function:var(--ease)]',
        cardVariants[variant],
        className,
      )}
      {...props}
    />
  )
)
Card.displayName = 'Card'

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col gap-1.5 p-6', className)} {...props} />
  )
)
CardHeader.displayName = 'CardHeader'

export const CardTitle = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3 ref={ref} className={cn('font-semibold leading-none tracking-tight text-[var(--text)]', className)} {...props} />
  )
)
CardTitle.displayName = 'CardTitle'

export const CardDescription = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p ref={ref} className={cn('text-sm text-[var(--text-2)]', className)} {...props} />
  )
)
CardDescription.displayName = 'CardDescription'

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('p-6 pt-0', className)} {...props} />
  )
)
CardContent.displayName = 'CardContent'

export const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex items-center p-6 pt-0', className)} {...props} />
  )
)
CardFooter.displayName = 'CardFooter'

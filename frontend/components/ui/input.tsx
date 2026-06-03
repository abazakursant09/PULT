import * as React from 'react'
import { cn } from '@/lib/utils'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        'flex h-[44px] w-full rounded-[8px]',
        'border border-[#1A1A1A] bg-[#0A0A0A]',
        'px-3 text-[15px] text-white',
        'placeholder:text-[#5A5A5A]',
        'transition-colors duration-200',
        'focus-visible:outline-none focus-visible:border-[#A78BFA]',
        'disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
      {...props}
    />
  )
)
Input.displayName = 'Input'

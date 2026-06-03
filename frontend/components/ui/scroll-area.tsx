import * as React from 'react'
import { cn } from '@/lib/utils'

interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {
  orientation?: 'vertical' | 'horizontal' | 'both'
}

export function ScrollArea({ className, orientation = 'vertical', children, ...props }: ScrollAreaProps) {
  return (
    <div
      className={cn(
        'relative overflow-hidden',
        orientation === 'vertical'   && 'overflow-y-auto',
        orientation === 'horizontal' && 'overflow-x-auto',
        orientation === 'both'       && 'overflow-auto',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

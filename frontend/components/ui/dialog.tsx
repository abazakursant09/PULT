'use client'
import * as React from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface DialogContextValue {
  open: boolean
  onOpenChange: (v: boolean) => void
}
const DialogCtx = React.createContext<DialogContextValue>({ open: false, onOpenChange: () => {} })

interface DialogProps {
  open?: boolean
  onOpenChange?: (v: boolean) => void
  children: React.ReactNode
}
export function Dialog({ open: controlledOpen, onOpenChange, children }: DialogProps) {
  const [internalOpen, setInternalOpen] = React.useState(false)
  const open   = controlledOpen !== undefined ? controlledOpen : internalOpen
  const change = (v: boolean) => { setInternalOpen(v); onOpenChange?.(v) }
  return <DialogCtx.Provider value={{ open, onOpenChange: change }}>{children}</DialogCtx.Provider>
}

export function DialogTrigger({ children, asChild }: { children: React.ReactNode; asChild?: boolean }) {
  const { onOpenChange } = React.useContext(DialogCtx)
  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(children as React.ReactElement<{ onClick?: React.MouseEventHandler }>, { onClick: () => onOpenChange(true) })
  }
  return <button type="button" onClick={() => onOpenChange(true)}>{children}</button>
}

export function DialogPortal({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

export function DialogOverlay({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { onOpenChange } = React.useContext(DialogCtx)
  return (
    <div
      className={cn('fixed inset-0 z-50 bg-[#1A1A1A]/60 backdrop-blur-sm', className)}
      onClick={() => onOpenChange(false)}
      {...props}
    />
  )
}

export function DialogContent({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { open, onOpenChange } = React.useContext(DialogCtx)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <DialogOverlay />
      <div
        className={cn(
          'relative z-50 w-full max-w-lg rounded-xl bg-background p-6 shadow-stripe-lg',
          'border border-border animate-blur-fade-in',
          className,
        )}
        onClick={e => e.stopPropagation()}
        {...props}
      >
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <X size={16} />
          <span className="sr-only">Close</span>
        </button>
        {children}
      </div>
    </div>
  )
}

export function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-1.5 text-center sm:text-left mb-4', className)} {...props} />
}

export function DialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col-reverse sm:flex-row sm:justify-end gap-2 mt-6', className)} {...props} />
}

export function DialogTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn('text-lg font-semibold leading-none tracking-tight', className)} {...props} />
}

export function DialogDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('text-sm text-muted-foreground', className)} {...props} />
}

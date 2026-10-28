'use client'
import * as React from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface DialogContextValue {
  open: boolean
  onOpenChange: (v: boolean) => void
  titleId?: string
}
const DialogCtx = React.createContext<DialogContextValue>({ open: false, onOpenChange: () => {} })

interface DialogProps {
  open?: boolean
  onOpenChange?: (v: boolean) => void
  children: React.ReactNode
}
export function Dialog({ open: controlledOpen, onOpenChange, children }: DialogProps) {
  const titleId = React.useId()
  const [internalOpen, setInternalOpen] = React.useState(false)
  const open   = controlledOpen !== undefined ? controlledOpen : internalOpen
  const change = (v: boolean) => { setInternalOpen(v); onOpenChange?.(v) }
  return <DialogCtx.Provider value={{ open, onOpenChange: change, titleId }}>{children}</DialogCtx.Provider>
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

const modalStack: HTMLElement[] = []

function focusableControls(root: HTMLElement) {
  return Array.from(root.querySelectorAll<HTMLElement>(
    'a[href],button,input,select,textarea,[tabindex],[contenteditable="true"]',
  )).filter(element => {
    if (element.tabIndex < 0 || element.matches(':disabled,input[type="hidden"]')) return false
    for (let current: HTMLElement | null = element; current; current = current.parentElement) {
      const style = window.getComputedStyle(current)
      if (current.hidden || current.hasAttribute('inert') || style.display === 'none' || style.visibility === 'hidden') return false
      if (current === root) break
    }
    return true
  })
}

export function DialogContent({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { open, onOpenChange, titleId } = React.useContext(DialogCtx)
  const contentRef = React.useRef<HTMLDivElement>(null)
  const previousFocusRef = React.useRef<HTMLElement | null>(null)
  const closeRef = React.useRef(onOpenChange)
  closeRef.current = onOpenChange
  React.useEffect(() => {
    const content = contentRef.current
    if (!open || !content) return
    const previous = previousFocusRef.current ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    const descendant = modalStack.findIndex(element => content.contains(element))
    if (descendant === -1) modalStack.push(content)
    else modalStack.splice(descendant, 0, content)
    const isTop = () => modalStack[modalStack.length - 1] === content
    const focusFirst = () => (focusableControls(content)[0] ?? content).focus({ preventScroll: true })
    if (!content.contains(document.activeElement)) focusFirst()
    const onFocus = (event: FocusEvent) => {
      if (content.isConnected && isTop() && event.target instanceof Node && !content.contains(event.target)) focusFirst()
    }
    const onKey = (event: KeyboardEvent) => {
      if (!content.isConnected || !isTop() || event.defaultPrevented) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closeRef.current(false)
      } else if (event.key === 'Tab') {
        const controls = focusableControls(content)
        const first = controls[0], last = controls[controls.length - 1]
        if (!first || !content.contains(document.activeElement) ||
            (event.shiftKey && (document.activeElement === first || document.activeElement === content)) ||
            (!event.shiftKey && document.activeElement === last)) {
          event.preventDefault()
          ;(event.shiftKey ? last ?? content : first ?? content).focus({ preventScroll: true })
        }
      }
    }
    document.addEventListener('focusin', onFocus)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('focusin', onFocus)
      document.removeEventListener('keydown', onKey)
      // A parent may unmount before its child effect cleans up. Restore the
      // connected outer opener rather than the child's now-removed trigger.
      const restoreFocus = isTop() || !modalStack[modalStack.length - 1]?.isConnected
      const index = modalStack.indexOf(content)
      if (index !== -1) modalStack.splice(index, 1)
      if (restoreFocus && previous?.isConnected) previous.focus({ preventScroll: true })
      previousFocusRef.current = null
    }
  }, [open])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <DialogOverlay />
      <div
        ref={contentRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={props['aria-label'] ? undefined : titleId}
        tabIndex={-1}
        className={cn(
          'relative z-50 w-full max-w-lg rounded-xl bg-background p-6 shadow-stripe-lg',
          'border border-border animate-blur-fade-in',
          className,
        )}
        onClick={e => e.stopPropagation()}
        {...props}
        onFocusCapture={event => {
          if (!previousFocusRef.current && event.relatedTarget instanceof HTMLElement &&
              !event.currentTarget.contains(event.relatedTarget)) previousFocusRef.current = event.relatedTarget
          props.onFocusCapture?.(event)
        }}
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
  const { titleId } = React.useContext(DialogCtx)
  return <h2 id={titleId} className={cn('text-lg font-semibold leading-none tracking-tight', className)} {...props} />
}

export function DialogDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('text-sm text-muted-foreground', className)} {...props} />
}

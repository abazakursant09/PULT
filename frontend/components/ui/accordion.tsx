'use client'
import * as React from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

interface AccordionContextValue {
  open: string | null
  toggle: (v: string) => void
  type: 'single' | 'multiple'
  openSet: Set<string>
}
const AccordionCtx = React.createContext<AccordionContextValue>({ open: null, toggle: () => {}, type: 'single', openSet: new Set() })

interface AccordionProps extends React.HTMLAttributes<HTMLDivElement> {
  type?: 'single' | 'multiple'
  defaultValue?: string
  collapsible?: boolean
}

export function Accordion({ type = 'single', defaultValue, collapsible = true, className, children, ...props }: AccordionProps) {
  const [open,    setOpen]    = React.useState<string | null>(defaultValue ?? null)
  const [openSet, setOpenSet] = React.useState<Set<string>>(new Set(defaultValue ? [defaultValue] : []))

  const toggle = (v: string) => {
    if (type === 'single') {
      setOpen(prev => (prev === v && collapsible ? null : v))
    } else {
      setOpenSet(prev => {
        const s = new Set(prev)
        s.has(v) ? s.delete(v) : s.add(v)
        return s
      })
    }
  }

  return (
    <AccordionCtx.Provider value={{ open, toggle, type, openSet }}>
      <div className={cn('w-full', className)} {...props}>{children}</div>
    </AccordionCtx.Provider>
  )
}

interface AccordionItemProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string
}
export function AccordionItem({ value, className, ...props }: AccordionItemProps) {
  return <div className={cn('border-b border-border', className)} data-value={value} {...props} />
}

interface AccordionTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string
}
export function AccordionTrigger({ value, className, children, ...props }: AccordionTriggerProps) {
  const { open, toggle, type, openSet } = React.useContext(AccordionCtx)
  const isOpen = type === 'single' ? open === value : openSet.has(value)
  return (
    <button
      type="button"
      onClick={() => toggle(value)}
      className={cn(
        'flex w-full items-center justify-between py-4 text-sm font-medium transition-all',
        'hover:underline text-left',
        '[&[data-state=open]>svg]:rotate-180',
        className,
      )}
      data-state={isOpen ? 'open' : 'closed'}
      {...props}
    >
      {children}
      <ChevronDown size={16} className={cn('shrink-0 text-muted-foreground transition-transform duration-200', isOpen && 'rotate-180')} />
    </button>
  )
}

interface AccordionContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string
}
export function AccordionContent({ value, className, children, ...props }: AccordionContentProps) {
  const { open, type, openSet } = React.useContext(AccordionCtx)
  const isOpen = type === 'single' ? open === value : openSet.has(value)
  if (!isOpen) return null
  return (
    <div
      className={cn('overflow-hidden text-sm', className)}
      {...props}
    >
      <div className="pb-4 pt-0">{children}</div>
    </div>
  )
}

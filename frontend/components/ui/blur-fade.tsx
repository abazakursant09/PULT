'use client'
import * as React from 'react'
import { cn } from '@/lib/utils'

interface BlurFadeProps {
  children: React.ReactNode
  className?: string
  delay?: number
  duration?: number
  yOffset?: number
  inView?: boolean
}

export function BlurFade({ children, className, delay = 0, duration = 0.4, yOffset = 6, inView = false }: BlurFadeProps) {
  const ref = React.useRef<HTMLDivElement>(null)
  const [visible, setVisible] = React.useState(inView)

  React.useEffect(() => {
    if (inView) { setVisible(true); return }
    const el = ref.current
    if (!el) return
    const obs = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { setVisible(true); obs.disconnect() } }, { threshold: 0.1 })
    obs.observe(el)
    return () => obs.disconnect()
  }, [inView])

  return (
    <div
      ref={ref}
      className={cn(className)}
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0) scale(1)' : `translateY(${yOffset}px) scale(0.98)`,
        filter: visible ? 'blur(0px)' : 'blur(4px)',
        transition: `opacity ${duration}s ease ${delay}s, transform ${duration}s ease ${delay}s, filter ${duration}s ease ${delay}s`,
      }}
    >
      {children}
    </div>
  )
}

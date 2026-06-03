'use client'
import * as React from 'react'

interface NumberTickerProps {
  value: number
  duration?: number
  delay?: number
  prefix?: string
  suffix?: string
  decimalPlaces?: number
  className?: string
}

export function NumberTicker({ value, duration = 1.5, delay = 0, prefix = '', suffix = '', decimalPlaces = 0, className }: NumberTickerProps) {
  const [current, setCurrent] = React.useState(0)
  const ref = React.useRef<HTMLSpanElement>(null)
  const started = React.useRef(false)

  React.useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !started.current) {
        started.current = true
        obs.disconnect()
        setTimeout(() => {
          const start = performance.now()
          const tick = (now: number) => {
            const t = Math.min((now - start) / (duration * 1000), 1)
            const eased = 1 - Math.pow(1 - t, 3)
            setCurrent(eased * value)
            if (t < 1) requestAnimationFrame(tick)
          }
          requestAnimationFrame(tick)
        }, delay * 1000)
      }
    }, { threshold: 0.5 })
    obs.observe(el)
    return () => obs.disconnect()
  }, [value, duration, delay])

  return (
    <span ref={ref} className={className}>
      {prefix}{current.toFixed(decimalPlaces)}{suffix}
    </span>
  )
}

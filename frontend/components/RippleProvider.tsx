'use client'
import { useEffect } from 'react'

export function RippleProvider() {
  useEffect(() => {
    function handler(e: MouseEvent) {
      const btn = (e.target as Element).closest('.btn') as HTMLButtonElement | null
      if (!btn || btn.disabled) return

      const rect = btn.getBoundingClientRect()
      const size = Math.max(rect.width, rect.height) * 2
      const x = e.clientX - rect.left - size / 2
      const y = e.clientY - rect.top - size / 2

      const ripple = document.createElement('span')
      ripple.className = 'ripple'
      ripple.style.cssText = `width:${size}px;height:${size}px;left:${x}px;top:${y}px`
      btn.appendChild(ripple)

      ripple.addEventListener('animationend', () => ripple.remove())
    }

    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [])

  return null
}

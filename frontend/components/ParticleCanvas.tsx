'use client'

import { useEffect, useRef } from 'react'

interface Node {
  x: number; y: number
  vx: number; vy: number
  r: number; a: number
  gold: boolean
  pulse: number
}

export function ParticleCanvas({ className = '' }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let rafId: number
    const nodes: Node[] = []
    const LINK_DIST = 150
    const LINK_DIST_SQ = LINK_DIST * LINK_DIST

    function resize() {
      if (!canvas) return
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = canvas.offsetWidth
      const h = canvas.offsetHeight
      canvas.width  = w * dpr
      canvas.height = h * dpr
      ctx!.scale(dpr, dpr)
      build(w, h)
    }

    function build(w: number, h: number) {
      nodes.length = 0
      const count = Math.min(Math.round((w * h) / 9000), 90)
      for (let i = 0; i < count; i++) {
        const gold = Math.random() < 0.3
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.12,
          vy: (Math.random() - 0.5) * 0.12,
          r: gold ? Math.random() * 1.8 + 0.8 : Math.random() * 1.0 + 0.3,
          a: gold ? Math.random() * 0.55 + 0.2 : Math.random() * 0.15 + 0.04,
          gold,
          pulse: Math.random() * Math.PI * 2,
        })
      }
    }

    function draw() {
      if (!canvas || !ctx) return
      const w = canvas.offsetWidth
      const h = canvas.offsetHeight
      ctx.clearRect(0, 0, w, h)

      for (const n of nodes) {
        n.x = ((n.x + n.vx) + w) % w
        n.y = ((n.y + n.vy) + h) % h
        n.pulse += 0.008
      }

      // connections
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x
          const dy = nodes[i].y - nodes[j].y
          const dSq = dx * dx + dy * dy
          if (dSq < LINK_DIST_SQ) {
            const t = 1 - Math.sqrt(dSq) / LINK_DIST
            const bothGold = nodes[i].gold && nodes[j].gold
            const alpha = bothGold ? t * 0.4 : t * 0.1
            ctx.beginPath()
            ctx.moveTo(nodes[i].x, nodes[i].y)
            ctx.lineTo(nodes[j].x, nodes[j].y)
            ctx.strokeStyle = `rgba(26,115,232,${alpha})`
            ctx.lineWidth = bothGold ? 0.8 : 0.4
            ctx.stroke()
          }
        }
      }

      // dots
      for (const n of nodes) {
        const pulsedR = n.gold ? n.r * (1 + Math.sin(n.pulse) * 0.12) : n.r
        const pulsedA = n.gold ? n.a * (0.85 + Math.sin(n.pulse) * 0.15) : n.a
        ctx.beginPath()
        ctx.arc(n.x, n.y, pulsedR, 0, Math.PI * 2)
        ctx.fillStyle = n.gold
          ? `rgba(26,115,232,${pulsedA})`
          : `rgba(240,239,234,${pulsedA})`
        ctx.fill()
      }

      rafId = requestAnimationFrame(draw)
    }

    resize()
    draw()

    const ro = new ResizeObserver(resize)
    ro.observe(canvas)
    window.addEventListener('resize', resize)
    return () => {
      cancelAnimationFrame(rafId)
      ro.disconnect()
      window.removeEventListener('resize', resize)
    }
  }, [])

  return (
    <canvas
      ref={ref}
      className={`absolute inset-0 w-full h-full pointer-events-none ${className}`}
    />
  )
}

'use client'
import { useState, useRef, useCallback } from 'react'

type Side = 'top' | 'bottom' | 'left' | 'right'

interface Props {
  text: string
  children: React.ReactNode
  side?: Side
  delay?: number
}

const OFFSET = 8

const tipStyle = (side: Side): React.CSSProperties => {
  const base: React.CSSProperties = {
    position: 'absolute',
    zIndex: 9999,
    background: 'rgba(26,26,26,0.92)',
    color: '#fff',
    fontSize: '0.72rem',
    fontWeight: 500,
    whiteSpace: 'nowrap',
    padding: '5px 9px',
    borderRadius: 7,
    pointerEvents: 'none',
    boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
    lineHeight: 1.4,
  }
  if (side === 'top')    return { ...base, bottom: `calc(100% + ${OFFSET}px)`, left: '50%', transform: 'translateX(-50%)' }
  if (side === 'bottom') return { ...base, top:    `calc(100% + ${OFFSET}px)`, left: '50%', transform: 'translateX(-50%)' }
  if (side === 'right')  return { ...base, left:   `calc(100% + ${OFFSET}px)`, top:  '50%', transform: 'translateY(-50%)' }
  return                          { ...base, right:  `calc(100% + ${OFFSET}px)`, top:  '50%', transform: 'translateY(-50%)' }
}

export function Tooltip({ text, children, side = 'top', delay = 200 }: Props) {
  const [show, setShow]  = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  const onEnter = useCallback(() => {
    timer.current = setTimeout(() => setShow(true), delay)
  }, [delay])

  const onLeave = useCallback(() => {
    clearTimeout(timer.current)
    setShow(false)
  }, [])

  if (!text) return <>{children}</>

  return (
    <div
      className="relative inline-flex items-center justify-center"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
    >
      {children}
      {show && (
        <div style={tipStyle(side)} role="tooltip" aria-label={text}>
          {text}
        </div>
      )}
    </div>
  )
}

'use client'
import { T } from '@/lib/tokens'

interface ErrorStateProps {
  message?:    string
  onRetry?:    () => void
  retryLabel?: string
  paddingTop?: number
}

export function ErrorState({
  message    = 'Данные временно недоступны',
  onRetry,
  retryLabel = 'Повторить',
  paddingTop = 64,
}: ErrorStateProps) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      gap: 12, paddingTop, paddingBottom: 48,
    }}>
      <span style={{ fontSize: T.sz.body, color: T.text3 }}>{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            fontSize: T.sz.caption,
            color:      T.vMid,
            background: 'none',
            border:     `1px solid ${T.vDim}`,
            borderRadius: T.r.btn,
            padding:    '6px 14px',
            cursor:     'pointer',
            letterSpacing: '0.01em',
          }}
        >
          {retryLabel}
        </button>
      )}
    </div>
  )
}

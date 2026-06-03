'use client'
import React from 'react'
import { T } from '@/lib/tokens'

interface EmptyStateProps {
  icon:     React.ReactNode
  title:    string
  body?:    string
  action?:  { label: string; onClick: () => void }
  paddingTop?: number
}

export function EmptyState({
  icon, title, body, action,
  paddingTop = 48,
}: EmptyStateProps) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      gap: 8, paddingTop, paddingBottom: 48, textAlign: 'center',
    }}>
      <div style={{ marginBottom: 4, opacity: 0.35 }}>{icon}</div>
      <p style={{
        fontSize: T.sz.heading, fontWeight: 600,
        color: T.text2, lineHeight: 1.3,
      }}>
        {title}
      </p>
      {body && (
        <p style={{
          fontSize: T.sz.caption, color: T.text3,
          maxWidth: 280, lineHeight: 1.5,
        }}>
          {body}
        </p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          style={{
            marginTop: 8,
            fontSize: T.sz.caption, color: T.vMid,
            background: T.vHint,
            border:     `1px solid ${T.vDim}`,
            borderRadius: T.r.btn,
            padding:    '7px 16px',
            cursor:     'pointer',
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  )
}

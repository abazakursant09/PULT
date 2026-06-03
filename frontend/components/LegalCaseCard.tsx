'use client'

import React, { useState } from 'react'
import { ChevronDown, ChevronUp, Shield, FileSearch, MessageSquare, CheckCircle2, AlertTriangle, AlertCircle } from 'lucide-react'
import { type LegalCase } from '@/lib/api'

const RISK_CONFIG = {
  high:   { color: '#1A73E8', label: 'Высокий',  icon: <AlertCircle   size={12} /> },
  medium: { color: '#1A73E8', label: 'Средний',  icon: <AlertTriangle size={12} /> },
  low:    { color: '#8A8986', label: 'Низкий',   icon: <CheckCircle2  size={12} /> },
}

const TYPE_CONFIG: Record<string, { label: string; icon: React.ReactElement }> = {
  card_audit:      { label: 'Аудит карточки', icon: <FileSearch    size={12} /> },
  review_response: { label: 'Анализ отзыва',  icon: <MessageSquare size={12} /> },
  review:          { label: 'Отзыв',          icon: <MessageSquare size={12} /> },
}

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  open:      { label: 'Открыт',    color: '#8A8986' },
  resolved:  { label: 'Решён',     color: '#1A73E8' },
  escalated: { label: 'Эскалация', color: '#1A73E8' },
  skipped:   { label: 'Пропущен',  color: 'rgba(255,255,255,0.15)' },
}

interface Props {
  legalCase:   LegalCase
  onResolve:   (id: string) => void
  onEscalate:  (id: string) => void
  onSaveResponse: (id: string, text: string) => void
}

export function LegalCaseCard({ legalCase: c, onResolve, onEscalate, onSaveResponse }: Props) {
  const [open, setOpen]         = useState(false)
  const [response, setResponse] = useState(c.user_response ?? '')
  const [saving, setSaving]     = useState(false)

  const risk   = RISK_CONFIG[c.risk_level]   ?? RISK_CONFIG.low
  const type   = TYPE_CONFIG[c.case_type]    ?? TYPE_CONFIG.card_audit
  const status = STATUS_CONFIG[c.status]     ?? STATUS_CONFIG.open

  async function save() {
    setSaving(true)
    await onSaveResponse(c.id, response)
    setSaving(false)
  }

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{
        background: '#F1F3F4',
        border: '1px solid rgba(26,115,232,0.1)',
        borderLeft: c.risk_level === 'low' ? '3px solid rgba(26,115,232,0.12)' : '3px solid rgba(26,115,232,0.4)',
      }}
    >
      {/* Header */}
      <button
        className="w-full flex items-start justify-between gap-3 px-5 py-4 text-left"
        onClick={() => setOpen(v => !v)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            {/* Risk badge */}
            <span
              className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
              style={{ background: `${risk.color}18`, color: risk.color, border: `1px solid ${risk.color}28` }}
            >
              {risk.icon}
              {risk.label} риск
            </span>
            {/* Type badge */}
            <span
              className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full"
              style={{ background: '#F1F3F4', color: '#8A8986', border: '1px solid rgba(26,115,232,0.12)' }}
            >
              {type.icon}
              {type.label}
            </span>
            {/* Status badge */}
            <span
              className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full"
              style={{ background: '#F1F3F4', color: status.color, border: '1px solid rgba(26,115,232,0.1)' }}
            >
              {status.label}
            </span>
          </div>
          <p className="text-sm font-semibold leading-snug" style={{ color: '#202124' }}>
            {c.title}
          </p>
        </div>
        <div className="shrink-0 mt-1" style={{ color: 'rgba(0,0,0,0.38)' }}>
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </button>

      {/* Expanded body */}
      {open && (
        <div className="px-5 pb-5 space-y-4">
          {/* Description */}
          <div
            className="px-4 py-3 rounded-xl text-sm leading-relaxed"
            style={{ background: '#F1F3F4', border: '1px solid rgba(26,115,232,0.1)', color: '#8A8986' }}
          >
            {c.description.split('\n').map((line, i) => (
              <span key={i}>{line}{i < c.description.split('\n').length - 1 && <br />}</span>
            ))}
          </div>

          {/* AI recommendation */}
          <div
            className="px-4 py-3 rounded-xl"
            style={{ background: 'rgba(26,115,232,0.05)', border: '1px solid rgba(26,115,232,0.12)' }}
          >
            <div className="flex items-center gap-2 mb-2">
              <Shield size={13} style={{ color: '#1A73E8' }} />
              <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: '#1A73E8' }}>
                Рекомендация ИИ
              </span>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: '#8A8986' }}>
              {c.ai_recommendation}
            </p>
          </div>

          {/* Response textarea */}
          {c.status !== 'resolved' && (
            <div>
              <label className="label mb-2">Ваш ответ / действие</label>
              <textarea
                rows={3}
                className="input w-full resize-none"
                placeholder="Опишите принятые меры или введите текст ответа..."
                value={response}
                onChange={e => setResponse(e.target.value)}
                style={{ fontFamily: 'inherit', fontSize: '0.875rem' }}
              />
            </div>
          )}

          {/* Actions */}
          {c.status !== 'resolved' && (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={save}
                disabled={saving || !response.trim()}
                className="btn btn-ghost text-xs px-3 py-2"
              >
                {saving ? 'Сохраняем...' : 'Сохранить ответ'}
              </button>
              <button
                onClick={() => onResolve(c.id)}
                className="btn btn-primary text-xs px-3 py-2"
              >
                <CheckCircle2 size={12} />
                Отметить решённым
              </button>
              {c.status !== 'escalated' && (
                <button
                  onClick={() => onEscalate(c.id)}
                  className="btn btn-ghost text-xs px-3 py-2"
                >
                  <AlertTriangle size={12} />
                  Эскалировать
                </button>
              )}
            </div>
          )}

          {c.status === 'resolved' && c.user_response && (
            <div
              className="px-4 py-3 rounded-xl text-sm"
              style={{ background: 'rgba(26,115,232,0.05)', border: '1px solid rgba(26,115,232,0.12)', color: '#8A8986' }}
            >
              <span className="font-semibold" style={{ color: '#1A73E8' }}>Принятые меры: </span>
              {c.user_response}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

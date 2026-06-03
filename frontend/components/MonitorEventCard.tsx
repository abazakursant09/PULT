'use client'

import { useState } from 'react'
import { ChevronDown, ChevronUp, AlertTriangle, Info, Zap,
         ShoppingBag, Store, MapPin, Scale } from 'lucide-react'
import { type MonitorEvent } from '@/lib/api'

interface Props { event: MonitorEvent }

const SEVERITY = {
  critical:  { label: 'Критически', color: '#DC2626', bg: 'rgba(220,38,38,0.08)',  border: 'rgba(220,38,38,0.25)'  },
  important: { label: 'Важно',      color: '#D97706', bg: 'rgba(217,119,6,0.08)',  border: 'rgba(217,119,6,0.22)'  },
  info:      { label: 'Инфо',       color: '#5F6368', bg: '#F1F3F4',   border: 'rgba(26,115,232,0.12)'     },
} as const

const SOURCE = {
  wildberries:  { label: 'Wildberries',     icon: ShoppingBag, color: '#8A8986' },
  ozon:         { label: 'Ozon',            icon: Store,       color: '#8A8986' },
  yandex_market:{ label: 'Яндекс Маркет',  icon: MapPin,      color: '#8A8986' },
  legislation:  { label: 'Законодательство', icon: Scale,      color: '#8A8986' },
} as const

const MODULE_LABEL: Record<string, string> = {
  pricing: 'Цены',
  reviews: 'Отзывы',
  legal:   'Юридическое',
  general: 'Общее',
}

function SeverityIcon({ s }: { s: MonitorEvent['severity'] }) {
  if (s === 'critical')  return <AlertTriangle size={13} />
  if (s === 'important') return <Zap           size={13} />
  return                        <Info          size={13} />
}

export function MonitorEventCard({ event }: Props) {
  const [open, setOpen] = useState(false)

  const sev = SEVERITY[event.severity] ?? SEVERITY.info
  const src = SOURCE[event.source]     ?? { label: event.source, icon: Info, color: '#8A8986' }
  const SrcIcon = src.icon

  return (
    <div className="card overflow-hidden">
      <div className="p-4 sm:p-5">
        {/* ── Badge row ── */}
        <div className="flex flex-wrap items-center gap-2 mb-3">
          {/* Severity */}
          <span
            className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-md"
            style={{ background: sev.bg, color: sev.color, border: `1px solid ${sev.border}` }}
          >
            <SeverityIcon s={event.severity} />
            {sev.label}
          </span>

          {/* Source */}
          <span
            className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-md"
            style={{ background: '#F1F3F4', color: src.color,
                     border: '1px solid rgba(26,115,232,0.12)' }}
          >
            <SrcIcon size={10} />
            {src.label}
          </span>

          {/* Module */}
          <span
            className="text-[10px] font-medium px-2 py-0.5 rounded-md"
            style={{ background: '#F1F3F4', color: '#8A8986',
                     border: '1px solid rgba(26,115,232,0.1)' }}
          >
            {MODULE_LABEL[event.affected_module] ?? event.affected_module}
          </span>

          {/* Date */}
          <span className="text-[10px] ml-auto shrink-0" style={{ color: 'rgba(0,0,0,0.38)' }}>
            {new Date(event.created_at).toLocaleString('ru-RU', {
              day: '2-digit', month: '2-digit', year: '2-digit',
              hour: '2-digit', minute: '2-digit',
            })}
          </span>
        </div>

        {/* ── Title ── */}
        <h3 className="text-sm font-semibold leading-snug mb-1.5" style={{ color: '#202124' }}>
          {event.title}
        </h3>

        {/* ── Description ── */}
        <p className="text-xs leading-relaxed" style={{ color: '#8A8986' }}>
          {event.description}
        </p>
      </div>

      {/* ── Action toggle ── */}
      <div style={{ borderTop: '1px solid rgba(26,115,232,0.1)' }}>
        <button
          onClick={() => setOpen(v => !v)}
          className="flex w-full items-center justify-between gap-2 px-4 sm:px-5 py-3
                     text-xs font-semibold transition-colors duration-150"
          style={{ color: open ? sev.color : '#9A9897' }}
        >
          <span>Что делать?</span>
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>

        {open && (
          <div
            className="px-4 sm:px-5 pb-4 text-xs leading-relaxed"
            style={{ color: '#8A8986', borderTop: '1px solid rgba(26,115,232,0.08)' }}
          >
            <div
              className="mt-3 p-3 rounded-xl"
              style={{ background: sev.bg, border: `1px solid ${sev.border}` }}
            >
              <p className="font-semibold mb-1" style={{ color: sev.color }}>Рекомендуемые действия:</p>
              <p style={{ color: '#202124' }}>{event.action_required}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

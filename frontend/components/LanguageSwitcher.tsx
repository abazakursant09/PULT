'use client'

import { useState, useRef, useEffect } from 'react'
import { ChevronDown, Languages } from 'lucide-react'
import { useLang } from '@/lib/lang-context'
import { LANGS, type Lang } from '@/lib/i18n'

interface Props {
  compact?: boolean  // icon-only for narrow sidebar
}

export function LanguageSwitcher({ compact }: Props) {
  const { lang, setLang, t } = useLang()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function close(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const current = LANGS.find(l => l.code === lang) ?? LANGS[0]

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 rounded-xl font-medium transition-all duration-200"
        style={{
          padding:     compact ? '7px' : '7px 10px',
          fontSize:    '0.8125rem',
          color:       '#6B6B6B',
          border:      '1px solid rgba(26,115,232,0.12)',
          background:  open ? 'rgba(26,115,232,0.06)' : 'transparent',
          borderColor: open ? 'rgba(26,115,232,0.2)'  : 'rgba(26,115,232,0.12)',
        }}
        title={t('lang.label')}
      >
        {compact ? (
          <Languages size={14} />
        ) : (
          <>
            <span style={{ fontSize: '1rem', lineHeight: 1 }}>{current.flag}</span>
            <span className="hidden lg:inline">{current.label}</span>
            <ChevronDown size={11} style={{ opacity: 0.5 }} />
          </>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className="absolute bottom-full mb-2 left-0 z-50 rounded-xl overflow-hidden shadow-lg"
            style={{
              background: '#F1F3F4',
              border:     '1px solid rgba(26,115,232,0.12)',
              minWidth:   148,
            }}
          >
            {LANGS.map(l => (
              <button
                key={l.code}
                onClick={() => { setLang(l.code as Lang); setOpen(false) }}
                className="flex items-center gap-3 w-full text-left px-4 py-2.5 transition-colors duration-100"
                style={{
                  fontSize:   '0.875rem',
                  fontWeight: lang === l.code ? 600 : 400,
                  color:      lang === l.code ? '#3B82F6' : '#1A1A1A',
                  background: lang === l.code ? 'rgba(26,115,232,0.06)' : 'transparent',
                }}
                onMouseEnter={e => { if (lang !== l.code) (e.currentTarget as HTMLElement).style.background = 'rgba(0,0,0,0.03)' }}
                onMouseLeave={e => { if (lang !== l.code) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
              >
                <span style={{ fontSize: '1.1rem' }}>{l.flag}</span>
                <span>{l.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

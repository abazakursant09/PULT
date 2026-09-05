'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { Check, Palette } from 'lucide-react'
import { THEME_IDS, THEME_OPTIONS, themeForKey } from '@/lib/theme'
import { useTheme } from '@/components/theme/ThemeProvider'

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const selectedOption = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const selected = THEME_OPTIONS.find((option) => option.id === theme) ?? THEME_OPTIONS[0]

  useEffect(() => {
    if (!open) return
    selectedOption.current?.focus()
    const onPointer = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    return () => {
      document.removeEventListener('mousedown', onPointer)
    }
  }, [open])

  return (
    <div
      className="theme-menu"
      ref={root}
      onKeyDown={(event) => {
        if (!open || event.key !== 'Escape') return
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
        trigger.current?.focus()
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="theme-menu__trigger"
        aria-label={`Тема: ${selected.name}`}
        aria-haspopup="dialog"
        aria-controls={menuId}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Palette size={15} />
        <span className="theme-menu__label">{selected.name}</span>
      </button>

      {open && (
        <div id={menuId} className="theme-menu__popover" role="dialog" aria-label="Выбор рабочей темы">
          <div className="theme-menu__title">Рабочая тема</div>
          <div role="radiogroup" aria-label="Тема оформления" onKeyDown={(event) => {
            const next = themeForKey(theme, event.key)
            if (!next) return
            event.preventDefault()
            setTheme(next)
            event.currentTarget.querySelectorAll('button')[THEME_IDS.indexOf(next)]?.focus()
          }}>
            {THEME_OPTIONS.map((option) => (
              <button
                ref={option.id === theme ? selectedOption : undefined}
                type="button"
                role="radio"
                aria-checked={option.id === theme}
                tabIndex={option.id === theme ? 0 : -1}
                className="theme-menu__item"
                key={option.id}
                onClick={() => { setTheme(option.id); setOpen(false); trigger.current?.focus() }}
              >
                <span className="theme-menu__swatches" aria-hidden="true">
                  {option.swatches.map((swatch) => <i key={swatch} style={{ background: swatch }} />)}
                </span>
                <span><strong>{option.name}</strong><small>{option.description}</small></span>
                {option.id === theme && <Check size={14} className="theme-menu__check" aria-hidden="true" />}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

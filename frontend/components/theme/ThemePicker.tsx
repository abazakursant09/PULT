'use client'

import { Check } from 'lucide-react'
import { THEME_IDS, THEME_OPTIONS, themeForKey } from '@/lib/theme'
import { useTheme } from './ThemeProvider'

export function ThemePicker() {
  const { theme, setTheme } = useTheme()

  return (
    <section className="theme-picker" aria-labelledby="theme-picker-title">
      <div className="theme-picker__head">
        <div>
          <p className="theme-picker__eyebrow">Рабочая среда</p>
          <h2 id="theme-picker-title">Оформление кабинета</h2>
          <p>Выбор хранится на этом устройстве. Цвета статусов остаются неизменными.</p>
        </div>
        <span className="theme-picker__status">Применяется сразу</span>
      </div>

      <div className="theme-picker__grid" role="radiogroup" aria-label="Тема оформления" onKeyDown={(event) => {
        const next = themeForKey(theme, event.key)
        if (!next) return
        event.preventDefault()
        setTheme(next)
        event.currentTarget.querySelectorAll('button')[THEME_IDS.indexOf(next)]?.focus()
      }}>
        {THEME_OPTIONS.map((option) => {
          const selected = option.id === theme
          return (
            <button
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              className="theme-choice"
              data-selected={selected || undefined}
              key={option.id}
              onClick={() => setTheme(option.id)}
            >
              <span className="theme-choice__preview" aria-hidden="true">
                {option.swatches.map((swatch) => <i key={swatch} style={{ background: swatch }} />)}
              </span>
              <span className="theme-choice__copy">
                <strong>{option.name}</strong>
                <small>{option.description}</small>
              </span>
              <span className="theme-choice__check" aria-hidden="true">{selected && <Check size={14} />}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}

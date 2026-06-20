'use client'
import { useEffect, useState } from 'react'

const KEY = 'pult_theme'
type Theme = 'dark' | 'light'

/** Применить тему синхронно (используется и в no-flash скрипте в layout). */
function apply(t: Theme) { document.documentElement.setAttribute('data-theme', t) }

/**
 * ThemeToggle — комфорт: тёмная (вечер/тёмная комната) ⇄ светлая (день, новичку привычнее).
 * Тема в localStorage. Дефолт — тёмная-комфорт (совпадает с :root).
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>('dark')

  useEffect(() => {
    const saved = (localStorage.getItem(KEY) as Theme | null) ?? 'dark'
    setTheme(saved); apply(saved)
  }, [])

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next); apply(next)
    try { localStorage.setItem(KEY, next) } catch { /* ignore */ }
  }

  return (
    <button onClick={toggle} aria-label="Сменить тему" style={{
      display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 600,
      color: 'var(--text-2)', background: 'var(--surface)', border: '1px solid var(--line)',
      borderRadius: 10, padding: '7px 12px', cursor: 'pointer',
    }}>
      <span>{theme === 'dark' ? '☀️' : '🌙'}</span>
      {theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
    </button>
  )
}

'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { applyTheme, DEFAULT_THEME, readStoredTheme, THEME_STORAGE_KEY, type ThemeId } from '@/lib/theme'

interface ThemeContextValue {
  theme: ThemeId
  setTheme: (theme: ThemeId) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(DEFAULT_THEME)

  useEffect(() => {
    const stored = readStoredTheme()
    setThemeState(stored)
    applyTheme(stored)
  }, [])

  const setTheme = useCallback((next: ThemeId) => {
    setThemeState(next)
    applyTheme(next)
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // Storage can be unavailable in a private or restricted browser context.
    }
  }, [])

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}

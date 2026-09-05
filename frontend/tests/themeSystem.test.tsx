import { readFileSync } from 'fs'
import { join } from 'path'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { ThemeToggle } from '@/components/cabinet/ThemeToggle'
import { ThemePicker } from '@/components/theme/ThemePicker'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { DEFAULT_THEME, THEME_IDS, THEME_OPTIONS, THEME_STORAGE_KEY } from '@/lib/theme'

const ROOT = join(__dirname, '..')
const read = (...path: string[]) => readFileSync(join(ROOT, ...path), 'utf8')

afterEach(() => {
  delete document.documentElement.dataset.sellerTheme
})

function renderWithTheme(child: React.ReactNode) {
  return render(<ThemeProvider>{child}</ThemeProvider>)
}

function missingThemeSelectors(css: string): string[] {
  return THEME_IDS.filter((id) => !css.includes(`[data-seller-theme="${id}"] .s-app`))
}

describe('seller theme contract', () => {
  it('ships the approved calm default and four explicit alternatives', () => {
    expect(THEME_STORAGE_KEY).toBe('pult_theme')
    expect(DEFAULT_THEME).toBe('champagne')
    expect(THEME_IDS).toEqual(['champagne', 'obsidian', 'titanium', 'pearl', 'system'])
    expect(THEME_OPTIONS).toHaveLength(5)
    expect(THEME_OPTIONS.find((option) => option.id === 'pearl')?.name).toBe('Жемчужное стекло')
  })

  it('restores a valid choice and falls back closed from invalid storage', async () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'pearl')
    const first = renderWithTheme(<ThemePicker />)
    await waitFor(() => expect(document.documentElement.dataset.sellerTheme).toBe('pearl'))
    expect(screen.getByRole('radio', { name: /Жемчужное стекло/ })).toHaveAttribute('aria-checked', 'true')
    first.unmount()

    localStorage.setItem(THEME_STORAGE_KEY, 'neon-rainbow')
    renderWithTheme(<ThemePicker />)
    await waitFor(() => expect(document.documentElement.dataset.sellerTheme).toBe('champagne'))
  })

  it('applies and persists a choice immediately', async () => {
    renderWithTheme(<ThemePicker />)
    fireEvent.click(screen.getByRole('radio', { name: /Титановый дым/ }))
    expect(document.documentElement.dataset.sellerTheme).toBe('titanium')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('titanium')
  })

  it('offers an accessible compact selector and restores focus on Escape', async () => {
    renderWithTheme(<ThemeToggle />)
    const trigger = screen.getByRole('button', { name: /Тема:/ })
    fireEvent.click(trigger)
    expect(screen.getByRole('dialog', { name: 'Выбор рабочей темы' })).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(5)
    fireEvent.keyDown(screen.getByRole('radiogroup'), { key: 'ArrowRight' })
    expect(screen.getByRole('radio', { name: /Обсидиан/ })).toHaveFocus()
    expect(document.documentElement.dataset.sellerTheme).toBe('obsidian')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(trigger).toHaveFocus()
  })

  it('supports keyboard navigation in the settings picker', () => {
    renderWithTheme(<ThemePicker />)
    fireEvent.keyDown(screen.getByRole('radiogroup'), { key: 'End' })
    expect(screen.getByRole('radio', { name: /Как в системе/ })).toHaveFocus()
    expect(document.documentElement.dataset.sellerTheme).toBe('system')
    fireEvent.keyDown(screen.getByRole('radiogroup'), { key: 'Home' })
    expect(document.documentElement.dataset.sellerTheme).toBe('champagne')
  })
})

describe('theme isolation and integration guards', () => {
  it('owns theme state in the reusable seller shell, not the public root', () => {
    const shell = read('components', 'seller', 'Shell.tsx')
    const root = read('app', 'layout.tsx')
    expect(shell).toContain('<ThemeProvider>')
    expect(shell).toContain('THEME_BOOTSTRAP_SCRIPT')
    expect(root).not.toContain('ThemeProvider')
    expect(root).toContain('suppressHydrationWarning')
  })

  it('exposes both the rail shortcut and full settings picker', () => {
    expect(read('components', 'seller', 'Shell.tsx')).toContain('<ThemeToggle />')
    expect(read('app', 'dashboard', 'settings', 'page.tsx')).toContain('<ThemePicker />')
  })

  it('scopes every palette to the cabinet and preserves semantic status tokens', () => {
    const css = read('styles', 'globals.css')
    expect(missingThemeSelectors(css)).toEqual([])
    const sellerThemeCss = css.slice(css.indexOf('/* Seller themes are scoped'))
    const palettes = sellerThemeCss.slice(0, sellerThemeCss.indexOf('/* ── BASE'))
    expect(palettes).not.toMatch(/--(?:success|danger|warning)\s*:/)
  })

  it.each(THEME_IDS)('mutation RED when the %s palette selector is removed', (id) => {
    const css = read('styles', 'globals.css')
    const mutated = css.replaceAll(`[data-seller-theme="${id}"] .s-app`, '[data-seller-theme="missing"] .s-app')
    expect(missingThemeSelectors(mutated)).toContain(id)
  })
})

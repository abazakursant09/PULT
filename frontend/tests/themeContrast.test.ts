import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(join(__dirname, '..', path), 'utf8')
const css = read('styles/globals.css')
const authCss = read('app/auth.module.css')

function authSurfaceDefects(source: string, stylesheet: string) {
  const defects: string[] = []
  if (!source.includes("import authStyles from '../auth.module.css'")) defects.push('missing-shared-surface')
  if (!source.includes('authStyles.surface')) defects.push('missing-scoped-root')
  if (/background:\s*['"]#[a-f\d]{3,8}\b/i.test(source)) defects.push('page-background-island')
  const root = stylesheet.match(/\.surface\s*\{([^}]+)\}/)?.[1] ?? ''
  for (const token of ['bg', 'surface', 'text', 'text-2', 'line', 'violet', 'primary-foreground']) {
    if (!new RegExp(`--${token}:`).test(root)) defects.push(`missing-${token}`)
  }
  if (/:root|\[data-seller-theme|:global\((?:body|html)\)/.test(stylesheet)) defects.push('theme-leak')
  return defects
}

function luminance(rgb: number[]) {
  const linear = rgb.map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
}
function hexRgb(hex: string) {
  return hex.match(/[a-f\d]{2}/gi)!.map(v => parseInt(v, 16) / 255)
}
function hslRgb(value: string) {
  const [h, sp, lp] = value.match(/[\d.]+/g)!.map(Number)
  const s = sp / 100, l = lp / 100
  const a = s * Math.min(l, 1 - l)
  return [0, 8, 4].map(n => {
    const k = (n + h / 30) % 12
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))
  })
}
function contrast(a: number[], b: number[]) {
  const x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

describe('theme primary action contrast', () => {
  for (const page of ['login', 'register', 'forgot-password', 'reset-password', 'verify-email']) {
    it(`${page} does not mix light-only page colors with shared dark cards`, () => {
      const source = read(`app/${page}/page.tsx`)
      expect(source).not.toMatch(/#(?:F6F9FC|F8F9FA|0A2540|202124|425466|8A8986)\b/i)
      expect(authSurfaceDefects(source, authCss)).toEqual([])
    })
  }
  it('auth text and primary actions remain readable on the owned light surface', () => {
    const root = authCss.match(/\.surface\s*\{([^}]+)\}/)![1]
    const token = (name: string) => hexRgb(root.match(new RegExp(`--${name}:\\s*(#[a-f\\d]{6})`, 'i'))![1])
    for (const name of ['text', 'text-2', 'text-3']) expect(contrast(token(name), token('bg'))).toBeGreaterThanOrEqual(4.5)
    for (const name of ['violet', 'violet-h']) expect(contrast([1, 1, 1], token(name))).toBeGreaterThanOrEqual(4.5)
  })
  it.each([
    ['surface token removed', (s: string) => s.replace('--surface:', '--retired-surface:')],
    ['scope leaks into seller themes', (s: string) => s + '\n[data-seller-theme="champagne"] { color: red; }'],
  ])('auth guard rejects %s', (_label, mutate) => {
    expect(authSurfaceDefects(read('app/register/page.tsx'), mutate(authCss))).not.toEqual([])
  })
  it('auth guard rejects a missing wrapper or a hardcoded page background', () => {
    const source = read('app/register/page.tsx')
    expect(authSurfaceDefects(source.replaceAll('authStyles.surface', 'retiredSurface'), authCss)).not.toEqual([])
    expect(authSurfaceDefects(source + "\nconst style = { background: '#09090B' }", authCss)).not.toEqual([])
  })
  it('settings connection notice inherits an existing theme surface without a light fallback', () => {
    const source = read('components/connections/ConnectionsSection.tsx')
    expect(source).toContain("background: 'var(--surface-h)'")
    expect(source).not.toContain('var(--surface-2')
    expect(source).not.toMatch(/#[a-f\d]{3,8}\b/i)
  })
  const blocks = [...css.matchAll(/\[data-seller-theme="([^"]+)"\] \.s-app\s*\{([^}]+)\}/g)]
  it('checks all palettes including both system variants', () => {
    expect(blocks.map(m => m[1])).toEqual(['champagne', 'obsidian', 'system', 'pearl', 'system'])
    expect(css).toContain('[data-seller-theme="titanium"] .s-app,')
  })
  for (const [index, match] of blocks.entries()) {
    it(`${match[1]} block ${index}: normal and hover text remain readable`, () => {
      const body = match[2]
      const fg = hslRgb(body.match(/--primary-foreground:([^;]+);/)![1])
      for (const token of ['violet', 'violet-h']) {
        const bg = hexRgb(body.match(new RegExp(`--${token}:(#[a-fA-F0-9]{6});`))![1])
        expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
      }
    })
  }
  it('shared and legacy primary buttons consume the foreground token', () => {
    expect(read('components/ui/button.tsx').match(/const _primary[^\n]+/)![0]).toContain('text-[hsl(var(--primary-foreground))]')
    expect(css.match(/\.btn-primary\s*\{([^}]+)\}/)![1]).toContain('hsl(var(--primary-foreground))')
  })
})

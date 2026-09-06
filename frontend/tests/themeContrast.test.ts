import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(join(__dirname, '..', path), 'utf8')
const css = read('styles/globals.css')

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

import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const ROOT = join(__dirname, '..')
const read = (path: string) => readFileSync(join(ROOT, path), 'utf8')

const SURFACES: Record<string, number> = {
  'app/page.tsx': 2,
  'app/login/page.tsx': 1,
  'app/register/page.tsx': 2,
  'app/forgot-password/page.tsx': 1,
  'app/reset-password/page.tsx': 1,
  'app/verify-email/page.tsx': 1,
  'components/seller/Shell.tsx': 1,
}

const CANONICAL_BODY = 'M7.25 2.75h13.5a4 4 0 0 1 4 4V29.25h-7.2V16.1'
const CANONICAL_FLOW = 'M5.05 5.35 12.15 11M22.95 5.35 12.15 11'
const CANONICAL_HUB = 'cx="12.15" cy="11" r="2.15"'
const IMPORT = "import { PultMark } from '@/components/brand/PultMark'"
const LETTER_BOX = /<(div|span)\b(?=[^>]*(?:background(?:Color)?\s*:|className\s*=\s*["'][^"']*\bbg-))[^>]*>\s*П\s*<\/\1>/

function circleAttribute(tag: string, name: string): string | undefined {
  return tag.match(new RegExp(`\\b${name}\\s*=\\s*["']([^"']+)["']`, 'i'))?.[1]
}

function hasConcentricTarget(source: string): boolean {
  const circles = [...source.matchAll(/<circle\b[^>]*>/gi)].map(match => ({
    cx: circleAttribute(match[0], 'cx'),
    cy: circleAttribute(match[0], 'cy'),
    r: Number(circleAttribute(match[0], 'r')),
  }))

  return circles.some((circle, index) => {
    if (!circle.cx || !circle.cy || !Number.isFinite(circle.r)) return false
    return circles.slice(index + 1).some(other =>
      other.cx === circle.cx
      && other.cy === circle.cy
      && Number.isFinite(other.r)
      && Math.max(circle.r, other.r) >= 4,
    )
  })
}

function markDefects(mark: string): string[] {
  const defects: string[] = []
  if (!mark.includes('viewBox="0 0 28 32"')) defects.push('viewBox')
  if (!mark.includes(CANONICAL_BODY)) defects.push('body')
  if (!mark.includes(CANONICAL_FLOW)) defects.push('flow')
  if (!mark.includes(CANONICAL_HUB)) defects.push('hub')
  if ((mark.match(/className="pult-mark-facet"/g) ?? []).length !== 3) defects.push('facets')
  if (hasConcentricTarget(mark)) defects.push('retired-target')
  return defects
}

function surfaceDefects(path: string, source: string): string[] {
  const defects: string[] = []
  if (!source.includes(IMPORT)) defects.push('import')
  if ((source.match(/<PultMark\b/g) ?? []).length !== SURFACES[path]) defects.push('usage-count')
  if (hasConcentricTarget(source)) defects.push('retired-target')
  if (/function\s+PultIcon\b/.test(source)) defects.push('local-copy')
  if (LETTER_BOX.test(source)) defects.push('letter-box')
  return defects
}

function faviconDefects(favicon: string): string[] {
  const defects: string[] = []
  if (!favicon.includes('viewBox="0 0 28 32"')) defects.push('viewBox')
  if (!favicon.includes(CANONICAL_BODY)) defects.push('body')
  if (!favicon.includes(CANONICAL_FLOW)) defects.push('flow')
  if (!favicon.includes(CANONICAL_HUB)) defects.push('hub')
  if (/<text\b/i.test(favicon)) defects.push('font-glyph')
  if (hasConcentricTarget(favicon)) defects.push('retired-target')
  return defects
}

function releasedShellNamingDefects(source: string): string[] {
  const defects: string[] = []
  if (!source.includes('<div className="pl">Пульт</div>')) defects.push('canonical-product-name')
  if (source.includes('Пульт OS')) defects.push('noncanonical-os-suffix')
  if (source.includes('Бизнес-Пульт')) defects.push('retired-product-name')
  return defects
}

describe('canonical PULT mark is fail-closed', () => {
  it('owns the approved П, converging facets, routed flow, and hub in one component', () => {
    expect(markDefects(read('components/brand/PultMark.tsx'))).toEqual([])
  })

  it('is the only brand mark used by every released surface', () => {
    for (const path of Object.keys(SURFACES)) {
      expect(surfaceDefects(path, read(path)), path).toEqual([])
    }
  })

  it('uses the same approved geometry in the favicon without a font glyph', () => {
    expect(faviconDefects(read('public/favicon.svg'))).toEqual([])
  })

  it.each([
    ['body geometry changed', () => markDefects(read('components/brand/PultMark.tsx').replace(CANONICAL_BODY, 'M0 0'))],
    ['routed flow removed', () => markDefects(read('components/brand/PultMark.tsx').replace(CANONICAL_FLOW, 'M0 0'))],
    ['hub moved', () => markDefects(read('components/brand/PultMark.tsx').replace(CANONICAL_HUB, 'cx="14" cy="12" r="2.15"'))],
    ['surface use removed', () => surfaceDefects('app/login/page.tsx', read('app/login/page.tsx').replace('<PultMark', '<div'))],
    ['target restored', () => surfaceDefects('app/login/page.tsx', `${read('app/login/page.tsx')}<circle cx="10" cy="10" r="8.25" /><circle cx="10" cy="10" r="4" />`)],
    ['reordered target restored', () => surfaceDefects('app/login/page.tsx', `${read('app/login/page.tsx')}<circle r="8.25" cy="10" cx="10" /><circle r="4" cx="10" cy="10" />`)],
    ['target inserted into canonical component', () => markDefects(`${read('components/brand/PultMark.tsx')}<circle r="8.25" cx="10" cy="10" /><circle cx="10" cy="10" r="4" />`)],
    ['letter box restored', () => surfaceDefects('app/register/page.tsx', `${read('app/register/page.tsx')}<div style={{ background: '#1A73E8' }}>П</div>`) ],
    ['tailwind letter box restored', () => surfaceDefects('app/register/page.tsx', `${read('app/register/page.tsx')}<div className="bg-blue-600 rounded-xl">П</div>`) ],
    ['favicon font glyph restored', () => faviconDefects(`${read('public/favicon.svg')}<text>П</text>`) ],
    ['favicon flow changed', () => faviconDefects(read('public/favicon.svg').replace(CANONICAL_FLOW, 'M0 0'))],
    ['target inserted into favicon', () => faviconDefects(`${read('public/favicon.svg')}<circle cx="10" cy="10" r="4"/><circle r="8.25" cy="10" cx="10"/>`)],
  ])('mutation RED: %s', (_name, mutate) => {
    expect(mutate()).not.toEqual([])
  })

  it.each([
    ['context color changes', () => surfaceDefects('app/login/page.tsx', read('app/login/page.tsx').replace('#E9E6FF', '#FFFFFF'))],
    ['unrelated icon circle remains allowed', () => surfaceDefects('app/login/page.tsx', `${read('app/login/page.tsx')}<circle cx="5" cy="5" r="2" />`)],
  ])('safe control GREEN: %s', (_name, mutate) => {
    expect(mutate()).toEqual([])
  })
})

describe('released seller shell uses the canonical product name', () => {
  const shell = () => read('components/seller/Shell.tsx')

  it('labels the active cabinet as Пульт', () => {
    expect(releasedShellNamingDefects(shell())).toEqual([])
  })

  it('fails closed if the retired Бизнес-Пульт label returns', () => {
    expect(releasedShellNamingDefects(shell().replace('Пульт</div>', 'Бизнес-Пульт</div>'))).not.toEqual([])
  })

  it('fails closed if the OS suffix is added', () => {
    expect(releasedShellNamingDefects(shell().replace('Пульт</div>', 'Пульт OS</div>'))).not.toEqual([])
  })
})

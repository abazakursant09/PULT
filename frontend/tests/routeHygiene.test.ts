import { existsSync, readFileSync, readdirSync, statSync } from 'fs'
import { join, relative } from 'path'
import { NextRequest } from 'next/server'
import { describe, expect, it } from 'vitest'

import { middleware } from '@/middleware'
import {
  AUTHENTICATED_TOOL_PREFIXES,
  BILLING_ROUTE_PREFIXES,
  GROWTH_ROUTE_PREFIXES,
  UNAVAILABLE_ROUTE_PREFIXES,
  isRouteUnavailable,
  matchesRoutePrefix,
} from '@/lib/routePolicy'

const ROOT = join(__dirname, '..')

function filesUnder(dir: string): string[] {
  if (!existsSync(dir)) return []
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? filesUnder(path) : [path]
  })
}

describe('route release policy is fail-closed', () => {
  it('pins the complete release inventory so a route cannot silently leave its gate', () => {
    expect(UNAVAILABLE_ROUTE_PREFIXES).toEqual([
      '/ad-strategy',
      '/auto-promotions',
      '/community',
      '/dashboard/finance',
      '/dashboard/leaks',
      '/dashboard/opportunities',
      '/dashboard/products',
      '/dashboard/risks',
      '/dashboard/seo',
      '/dashboard/sklad',
      '/dashboard/zakazy',
    ])
    expect(GROWTH_ROUTE_PREFIXES).toEqual([
      '/academy',
      '/ideas',
      '/market-overview',
      '/dashboard/deals',
      '/dashboard/referrals',
    ])
    expect(BILLING_ROUTE_PREFIXES).toEqual([
      '/checkout',
      '/dashboard/billing',
      '/payment/result',
      '/startup',
    ])
    expect(AUTHENTICATED_TOOL_PREFIXES).toEqual([
      '/academy',
      '/ai-agents',
      '/ideas',
      '/logistics',
      '/market-overview',
      '/profit-calculator',
      '/suppliers',
      '/admin',
    ])
  })

  it('blocks every placeholder regardless of feature flags', () => {
    for (const route of UNAVAILABLE_ROUTE_PREFIXES) {
      expect(isRouteUnavailable(route, { growthContour: true, billing: true })).toBe(true)
      expect(isRouteUnavailable(`${route}/child`, { growthContour: true, billing: true })).toBe(true)
    }
  })

  it('growth and billing routes require their independent flags', () => {
    for (const route of GROWTH_ROUTE_PREFIXES) {
      expect(isRouteUnavailable(route, { growthContour: false, billing: true })).toBe(true)
      expect(isRouteUnavailable(route, { growthContour: true, billing: true })).toBe(false)
    }
    for (const route of BILLING_ROUTE_PREFIXES) {
      expect(isRouteUnavailable(route, { growthContour: true, billing: false })).toBe(true)
      expect(isRouteUnavailable(route, { growthContour: true, billing: true })).toBe(false)
    }
  })

  it('matches a route boundary, not a lookalike prefix', () => {
    expect(matchesRoutePrefix('/academy/lesson', '/academy')).toBe(true)
    expect(matchesRoutePrefix('/academy-fake', '/academy')).toBe(false)
  })

  it('returns 404 before auth for a disabled route', () => {
    const response = middleware(new NextRequest('http://localhost/dashboard/products'))
    expect(response.status).toBe(404)
    expect(response.headers.get('location')).toBeNull()
  })

  it('requires a session for a released standalone seller tool', () => {
    const response = middleware(new NextRequest('http://localhost/logistics'))
    expect(response.status).toBeGreaterThanOrEqual(300)
    expect(response.headers.get('location')).toBe('http://localhost/login?from=%2Flogistics')
  })
})

describe('dead route residue is absent', () => {
  it('has no placeholder page or legacy navigation component', () => {
    for (const path of [
      'components/ComingSoon.tsx',
      'components/Sidebar.tsx',
      'components/DashboardTopBar.tsx',
      'app/ad-strategy/page.tsx',
      'app/auto-promotions/page.tsx',
      'app/community/page.tsx',
      'app/dashboard/finance/page.tsx',
      'app/dashboard/leaks/page.tsx',
      'app/dashboard/opportunities/page.tsx',
      'app/dashboard/products/page.tsx',
      'app/dashboard/products/[id]/page.tsx',
      'app/dashboard/risks/page.tsx',
      'app/dashboard/seo/page.tsx',
      'app/dashboard/sklad/page.tsx',
      'app/dashboard/zakazy/page.tsx',
    ]) expect(existsSync(join(ROOT, path)), path).toBe(false)
  })

  it('ships no clickable link to an always-unavailable route', () => {
    const candidates = [
      ...filesUnder(join(ROOT, 'app')),
      ...filesUnder(join(ROOT, 'components')),
      ...filesUnder(join(ROOT, 'lib')),
    ].filter(path => /\.(ts|tsx)$/.test(path) && !path.endsWith('routePolicy.ts'))

    for (const file of candidates) {
      const source = readFileSync(file, 'utf8')
      for (const route of UNAVAILABLE_ROUTE_PREFIXES) {
        const escaped = route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        const clickable = new RegExp(`(?:href|url)\\s*[:=]\\s*[\\"'\\x60]${escaped}(?:[/?\\"'\\x60])`)
        expect(source, `${relative(ROOT, file)} links to ${route}`).not.toMatch(clickable)
      }
    }
  })
})

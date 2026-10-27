import { NextRequest } from 'next/server'
import { unstable_doesMiddlewareMatch } from 'next/experimental/testing/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { config, middleware } from '@/middleware'

const { flags } = vi.hoisted(() => ({ flags: { billing: false, growthContour: false } }))
vi.mock('@/lib/featureFlags', () => ({ FLAGS: flags }))

beforeEach(() => { flags.billing = false; flags.growthContour = false })

const matches = (path: string) => unstable_doesMiddlewareMatch({
  config, nextConfig: {}, url: 'http://localhost' + path,
})

describe('file-looking application routes still execute release and session guards', () => {
  // Calling middleware() directly is insufficient: the old Next matcher skipped these paths.
  it.each(['/startup/1.js', '/startup/1.svg', '/startup/1.css', '/academy/lesson.png', '/dashboard/products/probe.js'])(
    'disabled path %s is matched and returns404 before session handling', path => {
      expect(matches(path)).toBe(true)
      const response = middleware(new NextRequest('http://localhost' + path))
      expect(response.status).toBe(404)
      expect(response.headers.get('location')).toBeNull()
    },
  )

  it.each(['/dashboard/stores/probe.svg', '/academy/lesson.png'])(
    'released protected path %s still requires a session', path => {
      flags.billing = true
      flags.growthContour = true
      expect(matches(path)).toBe(true)
      const response = middleware(new NextRequest('http://localhost' + path))
      expect(response.status).toBe(307)
      expect(new URL(response.headers.get('location')!).pathname).toBe('/login')
    },
  )

  it('released public onboarding retains its existing public access policy', () => {
    flags.billing = true
    expect(matches('/startup/1.js')).toBe(true)
    const response = middleware(new NextRequest('http://localhost/startup/1.js'))
    expect(response.headers.get('x-middleware-next')).toBe('1')
  })

  it.each(['/_next/static/chunks/app.js', '/_next/image?url=%2Flogo.png&w=64&q=75', '/favicon.ico'])(
    'real framework/static resource %s stays outside the matcher', path => {
      expect(matches(path)).toBe(false)
    },
  )

  it('public image assets remain publicly served through NextResponse.next', () => {
    const path = '/logos/wb.svg'
    const response = middleware(new NextRequest('http://localhost' + path))
    expect(response.status).toBe(200)
    expect(response.headers.get('x-middleware-next')).toBe('1')
  })
})

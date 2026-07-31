/**
 * SECURITY-2B-2 — structural guard: the frontend must never store, read, or send the session token.
 *
 * Scans production source (app / components / lib / hooks / middleware) for the old Bearer/localStorage
 * scheme. The only sanctioned reference is the one-time legacy PURGE in lib/session.ts (which DELETES a
 * pre-2B-2 token, never reads or sends one). Every API call must instead authenticate via the HttpOnly
 * cookie (credentials:'include').
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = join(__dirname, '..')
const DIRS = ['app', 'components', 'lib', 'hooks']
const FILES = ['middleware.ts']
const SESSION_TS = join('lib', 'session.ts').replace(/\\/g, '/')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const s = statSync(p)
    if (s.isDirectory()) {
      if (name === 'node_modules' || name === '.next') continue
      out.push(...walk(p))
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) {
      out.push(p)
    }
  }
  return out
}

function sources(): { rel: string; text: string }[] {
  const files: string[] = []
  for (const d of DIRS) files.push(...walk(join(ROOT, d)))
  for (const f of FILES) files.push(join(ROOT, f))
  return files.map(p => ({ rel: p.replace(ROOT + '\\', '').replace(ROOT + '/', '').replace(/\\/g, '/'),
                           text: readFileSync(p, 'utf8') }))
}

// key = pattern name, value = regex for a FORBIDDEN session-token usage
const FORBIDDEN: [string, RegExp][] = [
  ['localStorage token', /localStorage\.(get|set)Item\(\s*['"]token['"]/],
  ['sessionStorage token', /sessionStorage\.(get|set)Item\(\s*['"]token['"]/],
  ['Bearer auth header', /Authorization["']?\s*:\s*[`'"]\s*Bearer/],
  ['removed getToken helper', /\bgetToken\s*\(/],
  ['removed setToken helper (session)', /\bsetToken\s*\(\s*[a-zA-Z_]*access_token/],
]

describe('SECURITY-2B-2 frontend session guard', () => {
  const files = sources()

  it('finds source files to scan', () => {
    expect(files.length).toBeGreaterThan(20)
  })

  for (const [name, re] of FORBIDDEN) {
    it(`no "${name}" anywhere in production source`, () => {
      const hits = files.filter(f => re.test(f.text)).map(f => f.rel)
      expect(hits, `forbidden pattern "${name}" in: ${hits.join(', ')}`).toEqual([])
    })
  }

  it('the only pult_token reference is the legacy purge in lib/session.ts', () => {
    const hits = files.filter(f => /pult_token/.test(f.text)).map(f => f.rel)
    expect(hits).toEqual([SESSION_TS])
  })

  it('lib/session.ts exposes no token getter/setter', () => {
    const s = files.find(f => f.rel === SESSION_TS)!.text
    expect(/export function getToken/.test(s)).toBe(false)
    expect(/export function setToken/.test(s)).toBe(false)
    // it may only DELETE the legacy token, never read/store one
    expect(/localStorage\.setItem\(\s*['"]token['"]/.test(s)).toBe(false)
    expect(/localStorage\.getItem\(\s*['"]token['"]/.test(s)).toBe(false)
  })

  it('the API client sends the cookie via credentials:include and no Authorization', () => {
    const api = files.find(f => f.rel === 'lib/api.ts')!.text
    expect(/credentials:\s*'include'/.test(api)).toBe(true)
    // no ACTUAL Authorization header assignment (a comment mentioning the word is fine)
    expect(/h\['Authorization'\]|Authorization["']?\s*:/.test(api)).toBe(false)
  })
})

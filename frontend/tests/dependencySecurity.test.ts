import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import sharp from 'sharp'
import { describe, expect, it } from 'vitest'

// Reviewed compatibility slice, not a replacement for npm integrity verification
// or an assertion that the entire dependency graph has no other advisories.
const NEXT = '15.5.28'
const SHARP = '0.35.5'
const VIPS = '1.3.4'
const ROOT = join(__dirname, '..')
const requirePackage = createRequire(import.meta.url)
const SWC = ['darwin-arm64', 'darwin-x64', 'linux-arm64-gnu', 'linux-arm64-musl',
  'linux-x64-gnu', 'linux-x64-musl', 'win32-arm64-msvc', 'win32-x64-msvc']
const SHARP_TARGETS = ['@img/sharp-linux-x64', '@img/sharp-linuxmusl-x64',
  '@img/sharp-win32-x64', '@img/sharp-libvips-linux-x64', '@img/sharp-libvips-linuxmusl-x64']

type Manifest = { dependencies?: Record<string, string>; devDependencies?: Record<string, string> }
type Package = Manifest & {
  version?: string; resolved?: string; integrity?: string
  optionalDependencies?: Record<string, string>
}
type Lock = { lockfileVersion?: number; packages?: Record<string, Package> }
type Fixture = { manifest: Manifest; lock: Lock }

function securityDefects(manifest: Manifest, lock: Lock): string[] {
  const errors: string[] = []
  const packages = lock.packages ?? {}
  const root = packages[''] ?? {}
  const required = { next: NEXT, sharp: SHARP, 'eslint-config-next': NEXT }
  if (lock.lockfileVersion !== 3) errors.push('lock-version')
  for (const [name, version] of Object.entries(required)) {
    const section = name === 'eslint-config-next' ? 'devDependencies' : 'dependencies'
    if (manifest[section]?.[name] !== version) errors.push(`manifest:${name}`)
    if (root[section]?.[name] !== version) errors.push(`lock-root:${name}`)
    if (packages[`node_modules/${name}`]?.version !== version) errors.push(`lock:${name}`)
  }
  for (const name of ['@next/env', '@next/eslint-plugin-next', ...SWC.map(cpu => `@next/swc-${cpu}`)]) {
    if (packages[`node_modules/${name}`]?.version !== NEXT) errors.push(`missing-or-old:${name}`)
  }
  for (const name of SHARP_TARGETS) {
    const version = name.includes('libvips') ? VIPS : SHARP
    if (packages[`node_modules/${name}`]?.version !== version) errors.push(`missing-or-old:${name}`)
    if (packages['node_modules/sharp']?.optionalDependencies?.[name] !== version) {
      errors.push(`sharp-target:${name}`)
    }
  }
  for (const [path, record] of Object.entries(packages)) {
    const name = path.match(/(?:^|\/)node_modules\/(@[^/]+\/[^/]+|[^/]+)$/)?.[1]
    if (!name) continue
    const isNext = ['next', 'eslint-config-next', '@next/env', '@next/eslint-plugin-next'].includes(name)
      || name.startsWith('@next/swc-')
    const isSharp = name === 'sharp' || name.startsWith('@img/sharp-')
    if (!isNext && !isSharp) continue
    const version = isNext ? NEXT : name.startsWith('@img/sharp-libvips-') ? VIPS : SHARP
    if (record.version !== version) errors.push(`stale-or-shadowed:${path}`)
    if (!record.resolved?.startsWith('https://registry.npmjs.org/')) errors.push(`registry:${path}`)
    if (!/^sha512-[A-Za-z0-9+/]{86}==$/.test(record.integrity ?? '')) errors.push(`integrity:${path}`)
  }
  return errors
}

function validFixture(): Fixture {
  const manifest = { dependencies: { next: NEXT, sharp: SHARP },
    devDependencies: { 'eslint-config-next': NEXT } }
  const packages: Record<string, Package> = { '': structuredClone(manifest) }
  const add = (name: string, version: string) => {
    packages[`node_modules/${name}`] = { version,
      resolved: `https://registry.npmjs.org/${name}/-/fixture.tgz`,
      integrity: `sha512-${'A'.repeat(86)}==` }
  }
  for (const name of ['next', 'eslint-config-next', '@next/env', '@next/eslint-plugin-next',
    ...SWC.map(cpu => `@next/swc-${cpu}`)]) add(name, NEXT)
  add('sharp', SHARP)
  packages['node_modules/sharp'].optionalDependencies = {}
  for (const name of SHARP_TARGETS) {
    const version = name.includes('libvips') ? VIPS : SHARP
    add(name, version)
    packages['node_modules/sharp'].optionalDependencies![name] = version
  }
  return { manifest, lock: { lockfileVersion: 3, packages } }
}

describe('reviewed Next/sharp security graph', () => {
  it('pins matching patches, rejects shadowed vulnerable copies, and retains native targets', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
    const lock = JSON.parse(readFileSync(join(ROOT, 'package-lock.json'), 'utf8'))
    expect(securityDefects(manifest, lock)).toEqual([])
  })

  it('actually loads the reviewed framework and native image implementation', () => {
    expect(requirePackage('next/package.json').version).toBe(NEXT)
    expect(sharp.versions.sharp).toBe(SHARP)
  })

  const dangerous: [string, (fixture: Fixture) => void][] = [
    ['old Next manifest', f => { f.manifest.dependencies!.next = '15.5.22' }],
    ['old eslint config', f => { f.manifest.devDependencies!['eslint-config-next'] = '15.5.22' }],
    ['old sharp manifest', f => { f.manifest.dependencies!.sharp = '0.34.5' }],
    ['missing explicit sharp', f => { delete f.manifest.dependencies!.sharp }],
    ['stale root mirror', f => { f.lock.packages![''].dependencies!.next = '15.5.22' }],
    ['missing sharp record', f => { delete f.lock.packages!['node_modules/sharp'] }],
    ['nested old Next', f => { f.lock.packages!['node_modules/other/node_modules/next'] =
      { ...f.lock.packages!['node_modules/next'], version: '15.5.22' } }],
    ['nested old sharp', f => { f.lock.packages!['node_modules/next/node_modules/sharp'] =
      { ...f.lock.packages!['node_modules/sharp'], version: '0.34.5' } }],
    ['old SWC', f => { f.lock.packages!['node_modules/@next/swc-linux-x64-gnu'].version = '15.5.22' }],
    ['missing musl SWC', f => { delete f.lock.packages!['node_modules/@next/swc-linux-x64-musl'] }],
    ['old native sharp', f => { f.lock.packages!['node_modules/@img/sharp-win32-x64'].version = '0.34.5' }],
    ['old native libvips', f => { f.lock.packages!['node_modules/@img/sharp-libvips-linuxmusl-x64'].version = '1.2.4' }],
    ['missing musl sharp', f => { delete f.lock.packages!['node_modules/@img/sharp-linuxmusl-x64'] }],
    ['weakened sharp target', f => { f.lock.packages!['node_modules/sharp'].optionalDependencies!['@img/sharp-linuxmusl-x64'] = '0.34.5' }],
    ['URL escape', f => { f.lock.packages!['node_modules/next'].resolved = 'https://untrusted.invalid/next.tgz' }],
    ['missing integrity', f => { delete f.lock.packages!['node_modules/sharp'].integrity }],
  ]
  it.each(dangerous)('rejects %s', (_name, mutate) => {
    const fixture = validFixture()
    mutate(fixture)
    expect(securityDefects(fixture.manifest, fixture.lock).length).toBeGreaterThan(0)
  })

  it('accepts the intact fixture and ignores unrelated metadata without hiding security changes', () => {
    const fixture = validFixture()
    expect(securityDefects(fixture.manifest, fixture.lock)).toEqual([])
    fixture.lock.packages!['node_modules/unrelated'] = { version: '1.0.0' }
    expect(securityDefects(fixture.manifest, fixture.lock)).toEqual([])
  })
})

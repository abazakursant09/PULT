import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

const VITEST = '4.1.12'
const VITE = '6.4.5'
const ROOT = join(__dirname, '..')
const requirePackage = createRequire(import.meta.url)
type Entry = { version?: string; resolved?: string; integrity?: string; devDependencies?: Record<string, string> }
type Manifest = { devDependencies?: Record<string, string> }
type Lock = { lockfileVersion?: number; packages?: Record<string, Entry> }
type Fixture = { manifest: Manifest; lock: Lock }

// This reviewed runner graph removes Tinypool rather than overriding its major API.
// Integrity-shape checks supplement npm verification; they do not authenticate bytes.
function toolchainDefects(manifest: Manifest, lock: Lock): string[] {
  const errors: string[] = []
  const packages = lock.packages ?? {}
  if (lock.lockfileVersion !== 3) errors.push('lock-version')
  for (const [name, version] of Object.entries({ vitest: VITEST, vite: VITE })) {
    if (manifest.devDependencies?.[name] !== version) errors.push(`manifest:${name}`)
    if (packages['']?.devDependencies?.[name] !== version) errors.push(`lock-root:${name}`)
    if (packages[`node_modules/${name}`]?.version !== version) errors.push(`missing-or-old:${name}`)
  }
  for (const [path, entry] of Object.entries(packages)) {
    const name = path.match(/(?:^|\/)node_modules\/(@[^/]+\/[^/]+|[^/]+)$/)?.[1]
    if (!name) continue
    if (name === 'tinypool' || name === 'vite-node') errors.push(`removed-runner-dependency:${path}`)
    const isRunner = name === 'vitest' || name.startsWith('@vitest/')
    const isVite = name === 'vite'
    const isEsbuild = name === 'esbuild' || name.startsWith('@esbuild/')
    if (!isRunner && !isVite && !isEsbuild) continue
    if (isRunner && entry.version !== VITEST) errors.push(`runner-version:${path}`)
    if (isVite && entry.version !== VITE) errors.push(`vite-version:${path}`)
    if (isEsbuild && !/^0\.(?:2[5-9]|[3-9]\d)\.\d+$/.test(entry.version ?? '')) errors.push(`esbuild-version:${path}`)
    if (!entry.resolved?.startsWith('https://registry.npmjs.org/')) errors.push(`registry:${path}`)
    if (!/^sha512-[A-Za-z0-9+/]{86}==$/.test(entry.integrity ?? '')) errors.push(`integrity:${path}`)
  }
  return errors
}

function fixture(): Fixture {
  const manifest = { devDependencies: { vitest: VITEST, vite: VITE } }
  const packages: Record<string, Entry> = { '': structuredClone(manifest) }
  for (const [name, version] of Object.entries({ vitest: VITEST, vite: VITE,
    '@vitest/runner': VITEST, esbuild: '0.25.12', '@esbuild/win32-x64': '0.25.12' })) {
    packages[`node_modules/${name}`] = { version,
      resolved: `https://registry.npmjs.org/${name}/-/fixture.tgz`, integrity: `sha512-${'A'.repeat(86)}==` }
  }
  return { manifest, lock: { lockfileVersion: 3, packages } }
}

describe('reviewed test-toolchain security graph', () => {
  it('pins the patched runner and Vite without vulnerable shadow copies', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
    const lock = JSON.parse(readFileSync(join(ROOT, 'package-lock.json'), 'utf8'))
    expect(toolchainDefects(manifest, lock)).toEqual([])
  })

  it('actually resolves the single reviewed Vite implementation from Vitest', () => {
    expect(requirePackage('vitest/package.json').version).toBe(VITEST)
    expect(requirePackage('vite/package.json').version).toBe(VITE)
    const fromVitest = createRequire(requirePackage.resolve('vitest/package.json'))
    expect(fromVitest('vite/package.json').version).toBe(VITE)
    expect(requirePackage.resolve('vite/package.json')).toBe(fromVitest.resolve('vite/package.json'))
    expect(requirePackage('vitest/package.json').dependencies).not.toHaveProperty('tinypool')
    expect(requirePackage('vitest/package.json').dependencies).not.toHaveProperty('vite-node')
  })

  it('preserves async spies and explicit mock reset without opening a server', async () => {
    const api = { load: async () => 'original' }
    const spy = vi.spyOn(api, 'load').mockResolvedValue('synthetic')
    await expect(api.load()).resolves.toBe('synthetic')
    expect(spy).toHaveBeenCalledOnce()
    spy.mockRestore()
    await expect(api.load()).resolves.toBe('original')
    const standalone = vi.fn()
    standalone('one')
    standalone.mockReset()
    expect(standalone).not.toHaveBeenCalled()
  })

  const dangerous: [string, (data: Fixture) => void][] = [
    ['old Vitest manifest', f => { f.manifest.devDependencies!.vitest = '^1.6.1' }],
    ['floating Vite manifest', f => { f.manifest.devDependencies!.vite = '^6' }],
    ['old runner lock mirror', f => { f.lock.packages![''].devDependencies!.vitest = '^1.6.1' }],
    ['missing Vite lock mirror', f => { delete f.lock.packages![''].devDependencies!.vite }],
    ['missing runner', f => { delete f.lock.packages!['node_modules/vitest'] }],
    ['missing Vite', f => { delete f.lock.packages!['node_modules/vite'] }],
    ['old runner', f => { f.lock.packages!['node_modules/vitest'].version = '4.0.18' }],
    ['old Vite', f => { f.lock.packages!['node_modules/vite'].version = '6.4.3' }],
    ['nested stale Vitest', f => { f.lock.packages!['node_modules/other/node_modules/vitest'] =
      { ...f.lock.packages!['node_modules/vitest'], version: '1.6.1' } }],
    ['nested stale Vite', f => { f.lock.packages!['node_modules/vitest/node_modules/vite'] =
      { ...f.lock.packages!['node_modules/vite'], version: '5.4.21' } }],
    ['stale runner sibling', f => { f.lock.packages!['node_modules/@vitest/runner'].version = '1.6.1' }],
    ['Tinypool reintroduced', f => { f.lock.packages!['node_modules/tinypool'] = { version: '0.8.4' } }],
    ['nested Tinypool reintroduced', f => { f.lock.packages!['node_modules/other/node_modules/tinypool'] = { version: '1.1.1' } }],
    ['vite-node reintroduced', f => { f.lock.packages!['node_modules/vite-node'] = { version: '1.6.1' } }],
    ['old esbuild', f => { f.lock.packages!['node_modules/esbuild'].version = '0.21.5' }],
    ['old nested esbuild', f => { f.lock.packages!['node_modules/vite/node_modules/esbuild'] =
      { ...f.lock.packages!['node_modules/esbuild'], version: '0.24.2' } }],
    ['old native esbuild', f => { f.lock.packages!['node_modules/@esbuild/win32-x64'].version = '0.21.5' }],
    ['registry escape', f => { f.lock.packages!['node_modules/vitest'].resolved = 'https://untrusted.invalid/runner.tgz' }],
    ['missing integrity', f => { delete f.lock.packages!['node_modules/vite'].integrity }],
    ['weak integrity', f => { f.lock.packages!['node_modules/@vitest/runner'].integrity = 'sha1-unsupported' }],
    ['old lock format', f => { f.lock.lockfileVersion = 2 }],
  ]
  it.each(dangerous)('rejects %s', (_name, mutate) => {
    const data = fixture()
    mutate(data)
    expect(toolchainDefects(data.manifest, data.lock).length).toBeGreaterThan(0)
  })

  it('accepts reviewed records and unrelated metadata without weakening checks', () => {
    const data = fixture()
    expect(toolchainDefects(data.manifest, data.lock)).toEqual([])
    data.lock.packages!['node_modules/unrelated'] = { version: '1.0.0' }
    expect(toolchainDefects(data.manifest, data.lock)).toEqual([])
  })
})

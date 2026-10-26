import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Reviewed 8.x/3.x/1.x compatibility slice. Integrity-shape checks do not replace
// npm's checksum verification, and this is not a claim that all dev tools are safe.
const POSTCSS = '8.5.23'
const NANOID = '3.3.18'
const SOURCE_MAP = '1.2.2'
const EXPECTED = { postcss: POSTCSS, nanoid: NANOID, 'source-map-js': SOURCE_MAP }
const ROOT = join(__dirname, '..')
const requirePackage = createRequire(import.meta.url)
const nextRequire = createRequire(requirePackage.resolve('next/package.json'))
const postcssRequire = createRequire(nextRequire.resolve('postcss/package.json'))
const postcss = nextRequire('postcss')

type Manifest = {
  devDependencies?: Record<string, string>
  overrides?: Record<string, string | Record<string, string>>
}
type Entry = { version?: string; resolved?: string; integrity?: string; devDependencies?: Record<string, string> }
type Lock = { lockfileVersion?: number; packages?: Record<string, Entry> }
type Fixture = { manifest: Manifest; lock: Lock }

function cssSecurityDefects(manifest: Manifest, lock: Lock): string[] {
  const errors: string[] = []
  const packages = lock.packages ?? {}
  if (lock.lockfileVersion !== 3) errors.push('lock-version')
  if (manifest.devDependencies?.postcss !== POSTCSS) errors.push('manifest-postcss')
  if (packages['']?.devDependencies?.postcss !== POSTCSS) errors.push('lock-root-postcss')
  const scoped = manifest.overrides?.postcss
  if (!scoped || typeof scoped !== 'object') {
    errors.push('missing-scoped-override')
  } else {
    for (const [key, version] of Object.entries({ '.': '$postcss', nanoid: NANOID, 'source-map-js': SOURCE_MAP })) {
      if (scoped[key] !== version) errors.push(`override:${key}`)
    }
  }
  const found = new Set<string>()
  for (const [path, entry] of Object.entries(packages)) {
    const name = path.match(/(?:^|\/)node_modules\/(postcss|nanoid|source-map-js)$/)?.[1]
    if (!name) continue
    found.add(name)
    if (entry.version !== EXPECTED[name as keyof typeof EXPECTED]) errors.push(`stale-or-shadowed:${path}`)
    if (!entry.resolved?.startsWith('https://registry.npmjs.org/')) errors.push(`registry:${path}`)
    if (!/^sha512-[A-Za-z0-9+/]{86}==$/.test(entry.integrity ?? '')) errors.push(`integrity:${path}`)
  }
  for (const name of Object.keys(EXPECTED)) if (!found.has(name)) errors.push(`missing:${name}`)
  return errors
}

function fixture(): Fixture {
  const manifest: Manifest = { devDependencies: { postcss: POSTCSS }, overrides: {
    postcss: { '.': '$postcss', nanoid: NANOID, 'source-map-js': SOURCE_MAP },
  } }
  const packages: Record<string, Entry> = { '': { devDependencies: { postcss: POSTCSS } } }
  for (const [name, version] of Object.entries(EXPECTED)) {
    packages[`node_modules/${name}`] = { version, resolved: `https://registry.npmjs.org/${name}/-/fixture.tgz`,
      integrity: `sha512-${'A'.repeat(86)}==` }
  }
  return { manifest, lock: { lockfileVersion: 3, packages } }
}

const CSS = '.card{color:#242339;backdrop-filter:blur(12px);background:linear-gradient(120deg,#fff8,#eef2ff)}'
const MARKER = 'PULT_SYNTHETIC_MAP_CONTENT_NOT_FROM_USER_DATA'
const previousMap = { version: 3, file: 'output.css', names: [], sources: ['input.css'],
  sourcesContent: [MARKER], mappings: 'AAAA' }

describe('reviewed CSS dependency security graph', () => {
  it('pins the scoped override and every resolved copy, including shadowed packages', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
    const lock = JSON.parse(readFileSync(join(ROOT, 'package-lock.json'), 'utf8'))
    expect(cssSecurityDefects(manifest, lock)).toEqual([])
  })

  it('resolves the fixed implementation from Next and the build toolchain', () => {
    for (const resolve of [requirePackage, nextRequire]) {
      expect(resolve('postcss/package.json').version).toBe(POSTCSS)
      const nested = createRequire(resolve.resolve('postcss/package.json'))
      expect(nested('nanoid/package.json').version).toBe(NANOID)
      expect(nested('source-map-js/package.json').version).toBe(SOURCE_MAP)
    }
  })

  it('preserves ordinary author CSS without disabling parsing or maps globally', async () => {
    expect((await postcss([]).process(CSS, { from: 'input.css', map: false })).css).toBe(CSS)
  })

  it('does not auto-read an untrusted absolute map when from is unset', async () => {
    // One synthetic fixture only; no user file is read, no network is involved.
    const directory = mkdtempSync(join(tmpdir(), 'pult-css-map-'))
    const mapFile = join(directory, 'private.map')
    try {
      writeFileSync(mapFile, JSON.stringify(previousMap), { flag: 'wx', mode: 0o600 })
      const css = `${CSS}/*# sourceMappingURL=${mapFile.replace(/\\/g, '/')} */`
      const result = await postcss([]).process(css, { map: { inline: false, annotation: false } })
      expect((result.map?.toJSON().sourcesContent ?? []).join('\n')).not.toContain(MARKER)
    } finally {
      if (existsSync(mapFile)) unlinkSync(mapFile)
      rmdirSync(directory) // Exact mkdtemp directory; deliberately non-recursive.
    }
  })

  it('still accepts explicitly supplied, trusted source maps', async () => {
    const result = await postcss([]).process(CSS, { from: 'output.css',
      map: { prev: previousMap, inline: false, annotation: false } })
    expect((result.map?.toJSON().sourcesContent ?? []).join('\n')).toContain(MARKER)
  })

  it('bounds zero/negative nanoid sizes in a killable, isolated child', () => {
    const script = `const n=require(process.argv[1]),ns=require(process.argv[2]);
      if(n.customAlphabet('abc',0)()!=='')throw Error('customAlphabet zero');
      if(n.customRandom('abc',0,size=>Buffer.alloc(size))()!=='')throw Error('customRandom zero');
      if(ns.nanoid(-1)!==''||ns.customAlphabet('abc',-1)()!=='')throw Error('negative non-secure size');`
    const result = spawnSync(process.execPath, ['-e', script, postcssRequire.resolve('nanoid'),
      postcssRequire.resolve('nanoid/non-secure')], { encoding: 'utf8', timeout: 2000, killSignal: 'SIGKILL', maxBuffer: 65536 })
    expect(result.error).toBeUndefined()
    expect(result.status, result.stderr).toBe(0)
  })

  it('preserves valid source-map mapping and source content', () => {
    const { SourceMapGenerator, SourceMapConsumer } = postcssRequire('source-map-js')
    const generator = new SourceMapGenerator({ file: 'output.css' })
    generator.addMapping({ generated: { line: 1, column: 0 }, original: { line: 1, column: 0 }, source: 'input.css' })
    generator.setSourceContent('input.css', CSS)
    const consumer = new SourceMapConsumer(generator.toJSON())
    expect(consumer.originalPositionFor({ line: 1, column: 0 })).toMatchObject({ source: 'input.css', line: 1, column: 0 })
    expect(consumer.sourceContentFor('input.css')).toBe(CSS)
  })

  const dangerous: [string, (data: Fixture) => void][] = [
    ['old direct pin', f => { f.manifest.devDependencies!.postcss = '^8' }],
    ['old lock mirror', f => { f.lock.packages![''].devDependencies!.postcss = '^8' }],
    ['missing override', f => { delete f.manifest.overrides }],
    ['flattened override', f => { f.manifest.overrides!.postcss = POSTCSS }],
    ['wrong direct reference', f => { (f.manifest.overrides!.postcss as Record<string, string>)['.'] = '^8' }],
    ['old nanoid override', f => { (f.manifest.overrides!.postcss as Record<string, string>).nanoid = '3.3.11' }],
    ['old map override', f => { (f.manifest.overrides!.postcss as Record<string, string>)['source-map-js'] = '1.2.1' }],
    ['missing PostCSS', f => { delete f.lock.packages!['node_modules/postcss'] }],
    ['missing nanoid', f => { delete f.lock.packages!['node_modules/nanoid'] }],
    ['missing source-map-js', f => { delete f.lock.packages!['node_modules/source-map-js'] }],
    ['nested old PostCSS', f => { f.lock.packages!['node_modules/next/node_modules/postcss'] =
      { ...f.lock.packages!['node_modules/postcss'], version: '8.4.31' } }],
    ['nested old nanoid', f => { f.lock.packages!['node_modules/postcss/node_modules/nanoid'] =
      { ...f.lock.packages!['node_modules/nanoid'], version: '3.3.11' } }],
    ['nested old source-map-js', f => { f.lock.packages!['node_modules/other/node_modules/source-map-js'] =
      { ...f.lock.packages!['node_modules/source-map-js'], version: '1.2.1' } }],
    ['registry escape', f => { f.lock.packages!['node_modules/postcss'].resolved = 'https://untrusted.invalid/postcss.tgz' }],
    ['missing integrity', f => { delete f.lock.packages!['node_modules/nanoid'].integrity }],
    ['weak integrity', f => { f.lock.packages!['node_modules/source-map-js'].integrity = 'sha1-unsupported' }],
  ]
  it.each(dangerous)('rejects %s', (_name, mutate) => {
    const data = fixture()
    mutate(data)
    expect(cssSecurityDefects(data.manifest, data.lock).length).toBeGreaterThan(0)
  })

  it('accepts intact records and unrelated metadata without masking security changes', () => {
    const data = fixture()
    expect(cssSecurityDefects(data.manifest, data.lock)).toEqual([])
    data.lock.packages!['node_modules/unrelated'] = { version: '1.0.0' }
    expect(cssSecurityDefects(data.manifest, data.lock)).toEqual([])
  })
})

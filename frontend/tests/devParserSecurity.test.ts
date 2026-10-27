import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = join(__dirname, '..')
const requirePackage = createRequire(import.meta.url)
const VERSIONS: Record<string, string[]> = {
  'baseline-browser-mapping': ['2.11.0'], 'brace-expansion': ['1.1.21', '5.0.12'],
  browserslist: ['4.28.7'], 'js-yaml': ['4.3.2'], 'postcss-selector-parser': ['6.1.5'],
  'caniuse-lite': ['1.0.30001806'], 'electron-to-chromium': ['1.5.393'], 'node-releases': ['2.0.51'],
}
const OVERRIDES = {
  'baseline-browser-mapping': '2.11.0', 'brace-expansion@^1.0.0': '1.1.21',
  'brace-expansion@^5.0.0': '5.0.12', 'js-yaml': '4.3.2', 'postcss-selector-parser': '6.1.5',
  browserslist: { '.': '4.28.7', 'caniuse-lite': '1.0.30001806',
    'electron-to-chromium': '1.5.393', 'node-releases': '2.0.51' },
}
type Entry = { version?: string; resolved?: string; integrity?: string }
type Manifest = { overrides?: Record<string, string | Record<string, string>> }
type Lock = { lockfileVersion?: number; packages?: Record<string, Entry> }
type Fixture = { manifest: Manifest; lock: Lock }
const nameOf = (path: string) => path.match(/(?:^|\/)node_modules\/(@[^/]+\/[^/]+|[^/]+)$/)?.[1]

// Pin both brace API lines rather than forcing one major onto unrelated consumers.
// Integrity shape is not authenticity; npm ci and registry metadata provide that proof.
function parserDefects(manifest: Manifest, lock: Lock): string[] {
  const errors: string[] = []
  if (lock.lockfileVersion !== 3) errors.push('lock-version')
  for (const [name, value] of Object.entries(OVERRIDES)) {
    const actual = manifest.overrides?.[name]
    if (typeof value === 'string') {
      if (actual !== value) errors.push(`override:${name}`)
    } else {
      if (!actual || typeof actual !== 'object') errors.push(`override:${name}`)
      else for (const [child, version] of Object.entries(value)) {
        if (actual[child] !== version) errors.push(`override:${name}:${child}`)
      }
    }
  }
  const seen = new Set<string>()
  for (const [path, entry] of Object.entries(lock.packages ?? {})) {
    const name = nameOf(path)
    if (!name || !Object.hasOwn(VERSIONS, name)) continue
    seen.add(`${name}@${entry.version}`)
    if (!VERSIONS[name].includes(entry.version ?? '')) errors.push(`old-or-cross-major:${path}`)
    if (!entry.resolved?.startsWith('https://registry.npmjs.org/')) errors.push(`registry:${path}`)
    if (!/^sha512-[A-Za-z0-9+/]{86}==$/.test(entry.integrity ?? '')) errors.push(`integrity:${path}`)
  }
  for (const [name, versions] of Object.entries(VERSIONS)) for (const version of versions) {
    if (!seen.has(`${name}@${version}`)) errors.push(`missing:${name}@${version}`)
  }
  return errors
}

function fixture(): Fixture {
  const packages: Record<string, Entry> = { '': {} }
  for (const [name, versions] of Object.entries(VERSIONS)) versions.forEach((version, index) => {
    const path = index ? `node_modules/typed/node_modules/${name}` : `node_modules/${name}`
    packages[path] = { version, resolved: `https://registry.npmjs.org/${name}/-/fixture.tgz`,
      integrity: `sha512-${'A'.repeat(86)}==` }
  })
  return { manifest: { overrides: structuredClone(OVERRIDES) }, lock: { lockfileVersion: 3, packages } }
}

describe('reviewed development parser security graph', () => {
  it('pins every patched copy and preserves the two brace API major lines', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
    const lock = JSON.parse(readFileSync(join(ROOT, 'package-lock.json'), 'utf8'))
    expect(parserDefects(manifest, lock)).toEqual([])
  })

  it('actually installs the reviewed versions rather than relying on lock text alone', () => {
    const lock = JSON.parse(readFileSync(join(ROOT, 'package-lock.json'), 'utf8')) as Lock
    for (const [path, entry] of Object.entries(lock.packages ?? {})) {
      const name = nameOf(path)
      if (!name || !Object.hasOwn(VERSIONS, name)) continue
      const installed = JSON.parse(readFileSync(join(ROOT, path, 'package.json'), 'utf8'))
      expect(VERSIONS[name]).toContain(installed.version)
      expect(installed.version).toBe(entry.version)
    }
  })

  it('preserves ordinary YAML aliases and brace-based matching', () => {
    const yaml = requirePackage('js-yaml')
    expect(yaml.load('base: &base {enabled: true}\ncopy: *base\n')).toEqual({
      base: { enabled: true }, copy: { enabled: true },
    })
    expect(requirePackage('minimatch')('report-a.csv', 'report-{a,b}.csv')).toBe(true)
    const typed = createRequire(join(ROOT, 'node_modules/@typescript-eslint/typescript-estree/package.json'))
    expect(typed('minimatch').minimatch('report-b.csv', 'report-{a,b}.csv')).toBe(true)
  })

  it('bounds YAML empty merge sources with a small synthetic budget', () => {
    const yaml = requirePackage('js-yaml')
    const input = `sources: &sources [${Array(40).fill('{}').join(',')}]\ncopy:\n  <<: *sources\n`
    expect(() => yaml.load(input, { maxTotalMergeKeys: 10 })).toThrow(/merge|limit/i)
  })

  it('throws for conflicting baseline options without terminating its caller', () => {
    const script = `const m=require(process.argv[1]);let caught=false;
      try{m.getCompatibleVersions({targetYear:2023,widelyAvailableOnDate:'2024-01-01',suppressWarnings:true})}
      catch{caught=true}if(!caught)throw Error('invalid options accepted');console.log('CALLER_SURVIVED')`
    const child = spawnSync(process.execPath, ['-e', script, requirePackage.resolve('baseline-browser-mapping')],
      { encoding: 'utf8', timeout: 5000, killSignal: 'SIGKILL', maxBuffer: 65536 })
    expect(child.error).toBeUndefined()
    expect(child.status, child.stderr).toBe(0)
    expect(child.stdout).toContain('CALLER_SURVIVED')
  })

  it.each(['__proto__', 'toString', 'valueOf', 'constructor', 'hasOwnProperty', 'isPrototypeOf'])(
    'handles an own synthetic stats key %s without inherited-member crashes', key => {
      const browserslist = requirePackage('browserslist')
      const stats = JSON.parse(`{"${key}":{"onekey":5},"chrome":{"100":50}}`)
      expect(() => browserslist('defaults', { stats, path: ROOT })).not.toThrow()
    })

  it('preserves ordinary authored CSS selectors', () => {
    const selector = '.pult:where(.card,[data-theme="champagne"]) > .title'
    expect(requirePackage('postcss-selector-parser')().astSync(selector).toString()).toBe(selector)
  })

  const dangerous = Object.keys(OVERRIDES).map<[string, (data: Fixture) => void]>(name => [
    `missing override ${name}`, data => { delete data.manifest.overrides![name] },
  ])
  for (const [name, versions] of Object.entries(VERSIONS)) versions.forEach((_version, index) => {
    const path = index ? `node_modules/typed/node_modules/${name}` : `node_modules/${name}`
    const old: Record<string, string[]> = {
      'baseline-browser-mapping': ['2.10.22'], 'brace-expansion': ['1.1.14', '5.0.7'],
      browserslist: ['4.28.2'], 'js-yaml': ['4.1.1'], 'postcss-selector-parser': ['6.1.2'],
      'caniuse-lite': ['1.0.30001782'], 'electron-to-chromium': ['1.5.328'], 'node-releases': ['2.0.36'],
    }
    dangerous.push([`stale ${name} copy ${index}`, data => { data.lock.packages![path].version = old[name][index] }])
  })
  dangerous.push(
    ['missing brace5 branch', data => { delete data.lock.packages!['node_modules/typed/node_modules/brace-expansion'] }],
    ['nested stale selector', data => { data.lock.packages!['node_modules/other/node_modules/postcss-selector-parser'] =
      { ...data.lock.packages!['node_modules/postcss-selector-parser'], version: '6.1.2' } }],
    ['nested stale YAML', data => { data.lock.packages!['node_modules/other/node_modules/js-yaml'] =
      { ...data.lock.packages!['node_modules/js-yaml'], version: '4.1.1' } }],
    ['flat browser override', data => { data.manifest.overrides!.browserslist = '4.28.7' }],
    ['stale browser table pin', data => { (data.manifest.overrides!.browserslist as Record<string, string>)['caniuse-lite'] = '^1' }],
    ['registry escape', data => { data.lock.packages!['node_modules/browserslist'].resolved = 'https://untrusted.invalid/data.tgz' }],
    ['missing integrity', data => { delete data.lock.packages!['node_modules/js-yaml'].integrity }],
    ['weak integrity', data => { data.lock.packages!['node_modules/brace-expansion'].integrity = 'sha1-unsupported' }],
    ['old lock format', data => { data.lock.lockfileVersion = 2 }],
  )
  it.each(dangerous)('rejects %s', (_name, mutate) => {
    const data = fixture()
    mutate(data)
    expect(parserDefects(data.manifest, data.lock).length).toBeGreaterThan(0)
  })

  it('accepts complete records and unrelated metadata without hiding security changes', () => {
    const data = fixture()
    expect(parserDefects(data.manifest, data.lock)).toEqual([])
    data.lock.packages!['node_modules/unrelated'] = { version: '1.0.0' }
    for (const name of ['constructor', '__proto__', 'toString', 'valueOf', 'hasOwnProperty']) {
      data.lock.packages![`node_modules/${name}`] = { version: '1.0.0' }
    }
    expect(parserDefects(data.manifest, data.lock)).toEqual([])
  })
})

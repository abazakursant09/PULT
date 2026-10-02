import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { createHash } from 'crypto'
import { expect, it } from 'vitest'

const root = join(__dirname, '..')
const assets: Record<string, string> = {
  'ibmplexmono/IBMPlexMono-Medium.ttf': 'a9b4c49bb299e05b5f6c481e7fb5e78943d2793249a0c8874ab574a2d1ea6755',
  'ibmplexmono/IBMPlexMono-Regular.ttf': '6a3412f058c7d8dfd9170c41e85ade48e5156ecb89356110ca57a0a27734af46',
  'ibmplexsans/IBMPlexSans[wdth,wght].ttf': '3b031aa4216174205bd8471f88a49b91f093169e9e87bd5262242bc5967fe2e3',
  'inter/Inter[opsz,wght].woff2': 'b8f22074e7831a5e12280ca68f75735709c8ab587ead5a82c3544837b5aa4124',
  'jetbrainsmono/JetBrainsMono[wght].woff2': 'ede9d67aa34f2b6f20214132b90afc706ea74ce998930fc346974f2ebf72d1fc',
  'sourceserif4/SourceSerif4[opsz,wght].woff2': '8158f5550b9fe9af7734a5985a2bc7318c3dd7c6937d28f46bb5d2bc5a735269',
}

it('ships the pinned font binaries with their licenses', () => {
  for (const [path, hash] of Object.entries(assets)) {
    const bytes = readFileSync(join(root, 'fonts', path))
    expect(createHash('sha256').update(bytes).digest('hex'), path).toBe(hash)
    expect(readFileSync(join(root, 'fonts', path.split('/')[0], 'OFL.txt'), 'utf8')).toContain('SIL OPEN FONT LICENSE')
  }
})

it('has no build-time Google font imports in app or components', () => {
  function walk(dir: string) {
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry)
      if (statSync(path).isDirectory()) walk(path)
      else if (/\.[jt]sx?$/.test(entry)) expect(readFileSync(path, 'utf8'), path).not.toMatch(/next\/font\/google|@next\/font\/google/)
    }
  }
  walk(join(root, 'app'))
  walk(join(root, 'components'))
})

it('keeps local sources, swap and the five public font variables', () => {
  for (const [path, variables] of [
    ['app/layout.tsx', ['--font-inter', '--font-mono']],
    ['components/stores/ledgerFonts.ts', ['--font-ledger-serif', '--font-ledger-sans', '--font-ledger-mono']],
  ] as const) {
    const code = readFileSync(join(root, path), 'utf8')
    expect(code).toContain("from 'next/font/local'")
    for (const name of variables) expect(code).toContain(name)
    expect(code.match(/display: 'swap'/g)).toHaveLength(variables.length)
  }
})

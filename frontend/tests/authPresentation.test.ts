import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

// Contract captured from master 2036059 before presentation edits. JSX styling is
// excluded, while non-view logic, security imports, form wiring and conditional
// view gates remain pinned. Behavioral auth tests still run independently.
function captureContract(source: string) {
  const file = ts.createSourceFile('page.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const printer = ts.createPrinter({ removeComments: true })
  const canonical = (node: ts.Node): string => {
    const result = ts.transform(node, [context => root => {
      const visit: ts.Visitor = n => ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)
        ? ts.factory.createNull() : ts.visitEachChild(n, visit, context)
      return ts.visitNode(root, visit) as typeof root
    }])
    const text = printer.printNode(ts.EmitHint.Unspecified, result.transformed[0], file)
    result.dispose()
    return text
  }
  const attributes: string[] = []
  const gates: string[] = []
  const protectedNames = /^(onSubmit|onChange|onClick|disabled|required|loading|method|href|target|rel|type|name|id|htmlFor|value|checked|autoComplete|pattern|maxLength|inputMode|onValid)$/
  function walk(node: ts.Node) {
    if (ts.isJsxAttribute(node) && protectedNames.test(node.name.getText(file))) attributes.push(canonical(node))
    if (ts.isJsxExpression(node) && node.expression && !ts.isJsxAttribute(node.parent)) {
      let containsView = false
      const findView = (child: ts.Node) => {
        if (ts.isJsxElement(child) || ts.isJsxSelfClosingElement(child) || ts.isJsxFragment(child)) containsView = true
        else ts.forEachChild(child, findView)
      }
      findView(node.expression)
      if (containsView) gates.push(canonical(node.expression))
    }
    ts.forEachChild(node, walk)
  }
  walk(file)
  const code = file.statements.filter(n => !ts.isImportDeclaration(n)).map(canonical)
  const securityImports = file.statements.filter(ts.isImportDeclaration)
    .filter(n => /\/(api|session|lang-context)$/.test((n.moduleSpecifier as ts.StringLiteral).text)).map(canonical)
  const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex')
  return { code: hash(code), attributes: hash(attributes), gates: hash(gates), securityImports: hash(securityImports) }
}

const BASELINES: Record<string, ReturnType<typeof captureContract>> = {
  "login": {
    "code": "b60a55ade0e437a02c883c71549d89457e9e6b1e7f5367a672f5afd9e2d99c5f",
    "attributes": "6066d56cf9754527a655308eae0f773135886e2c6531789584eb3cadefebd7a3",
    "gates": "d1e5d14cfe9f315569f84e2d123459fd788c67ab73d3cb27ffec95b07496cc2b",
    "securityImports": "a0d7b16720f5d9d398a28e32303698eb64c4a8408115b77b11060b06d521cd2f"
  },
  "register": {
    "code": "1be178962fea351f7864c4fe99814c69f2531e429b3c224b55949f5e59174253",
    "attributes": "a2b26cbe0ebd967c10719c7096d72dba531ffeda18572c0c48dc902bb0fdb93e",
    "gates": "585e2895b9efc635f0423164193729f72ab36e5da70f16a94cc82a4c93ef3c83",
    "securityImports": "6c0a1ca66033c181c78f070bee92f16b5856e1df9db0daf622ce159c5f895b64"
  },
  "forgot-password": {
    "code": "17ae7092c9fceffe768bd8b5fe8d27a2cb73c37dbeff1c451acfd4f28fca0f9b",
    "attributes": "e6f7492f4911fa60fbcd3b8385b50cdcb2468f76afc033fdc3cf1d775fae7b81",
    "gates": "b2543400ebe9256d585ee2a5fc5edc5d9f62e41f515031d14c042f8e06f6c637",
    "securityImports": "42612563ab59362d79c6ca257ed14c6d8669ed8124eff4f15ae03d18400d1225"
  },
  "reset-password": {
    "code": "fc445c7fb080a0130d428db9842acda9f5dc6d7a0bed7db38a017187e8b19553",
    "attributes": "f08e2af00dd9c436004a914ca7af584e9c45d6dfc2e1b7b20e3b65be988503fd",
    "gates": "77a28f4afbb3fedde4aa2386080437b12d7134b3b11d2ececf3430ebc5196e1e",
    "securityImports": "42612563ab59362d79c6ca257ed14c6d8669ed8124eff4f15ae03d18400d1225"
  },
  "verify-email": {
    "code": "545af962112a4cf7c2b5299affa3c0ee453a47471f14d1cb4220d0b71ea5df81",
    "attributes": "ce9ad965d67e18b9a8e4341f89b62728e3a8cd86658e6d083d426dc385fb277f",
    "gates": "c3b7ab4527028cb57491ba10bb158e2315b41e8e0f812779973b12b21a31269d",
    "securityImports": "6cde33d0894f477fc8732d7dce24abf136705f730eb96d57e0317c654a3e7ff2"
  }
}

describe('Auth presentation preserves existing behavior', () => {
  for (const name of ['login', 'register', 'forgot-password', 'reset-password', 'verify-email']) {
    const source = () => readFileSync(`app/${name}/page.tsx`, 'utf8')
    it(`${name}: retains non-view logic, form wiring and state gates`, () => {
      expect(captureContract(source())).toEqual(BASELINES[name])
    })
    it(`${name}: uses the scoped light surface and immutable approved mark`, () => {
      expect(source()).toContain('authStyles.surface')
      expect(source()).not.toMatch(/<PultMark\b(?![^>]*\bapprovedReference)[^>]*>/)
    })
  }
  it('detects a changed login API method', () => {
    const source = readFileSync('app/login/page.tsx', 'utf8')
    expect(captureContract(source.replace('api.auth.login(', 'api.auth.register(')).code).not.toBe(BASELINES.login.code)
  })
  it('detects removal of registration submit gating', () => {
    const source = readFileSync('app/register/page.tsx', 'utf8')
    expect(captureContract(source.replace('disabled={!agreed || !agreedTerms || !captchaOk}', 'disabled={false}')).attributes).not.toBe(BASELINES.register.attributes)
  })
})

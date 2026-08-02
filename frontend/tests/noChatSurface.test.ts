import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// LEGAL-1A guard — the user-to-user "Биржа" chat is removed from the frontend.
//
// The Биржа was real user-to-user messaging (149-FZ art. 10.1 ОРИ exposure). This test fails if any
// of its surface reappears: the /dashboard/chat page, the api.chat client, the ChatMessage type, or
// a visible "Биржа" screen.
//
// It deliberately does NOT forbid the substring "chat": telegram_chat_id / getChatId / "Chat ID" are
// a different, allowed feature (user↔bot notifications). Only the exchange-specific tokens are banned.

const ROOT = join(__dirname, '..')

function sourcesUnder(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.next' || entry.startsWith('.')) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...sourcesUnder(full))
    else if (/\.tsx?$/.test(entry)) out.push(full)
  }
  return out
}

const FILES = [
  ...sourcesUnder(join(ROOT, 'app')),
  ...sourcesUnder(join(ROOT, 'components')),
  ...sourcesUnder(join(ROOT, 'lib')),
]

const read = (f: string) => readFileSync(f, 'utf-8')

const FORBIDDEN: { token: RegExp; label: string }[] = [
  { token: /\/dashboard\/chat/, label: '/dashboard/chat route' },
  { token: /\/api\/chat\b/, label: '/api/chat endpoint' },
  { token: /\bapi\.chat\b/, label: 'api.chat client' },
  { token: /\bChatMessage\b/, label: 'ChatMessage type' },
  { token: /\bSendMessageResult\b/, label: 'SendMessageResult type' },
  { token: /Биржа/, label: '"Биржа" UI text' },
]

describe('LEGAL-1A: no chat surface in the frontend', () => {
  it('has no /dashboard/chat page directory', () => {
    let exists = true
    try {
      statSync(join(ROOT, 'app', 'dashboard', 'chat'))
    } catch {
      exists = false
    }
    expect(exists).toBe(false)
  })

  for (const { token, label } of FORBIDDEN) {
    it(`does not reference ${label} anywhere in app/components/lib`, () => {
      const offenders = FILES.filter((f) => token.test(read(f))).map((f) => f.slice(ROOT.length + 1))
      expect(offenders).toEqual([])
    })
  }
})

import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'
import { getT, LANGS } from '../lib/i18n'

describe('auth presentation copy', () => {
  for (const page of ['login', 'register', 'forgot-password', 'reset-password', 'verify-email']) {
    it(`${page} uses the Pult brand without the legacy prefix`, () => {
      const source = readFileSync(join(__dirname, '..', 'app', page, 'page.tsx'), 'utf8')
      expect(source).not.toMatch(/Бизнес[‑–-]/)
      expect(source).toContain('PultMark')
    })
  }
  for (const { code } of LANGS) {
    it(`${code} registration does not promise an unapproved trial or payment terms`, () => {
      const t = getT(code)
      const copy = [t('register.subtitle'), t('register.tagline')].join(' ')
      expect(copy).not.toMatch(/14|бесплат|карт|платеж|отмен|free|card|fees|cancel|免费|信用卡|取消|费用/i)
    })
  }
})

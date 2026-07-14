import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// The privacy policy is the document a seller consents to at registration, so it must (a) name
// the personal-data operator and (b) describe deletion the way the product actually behaves —
// a soft-delete plus a manual erasure-on-request, NOT an automatic hard delete the code does not
// perform. These tests fail the day the wording drifts from the implementation again.

const ROOT = join(__dirname, '..')
const privacy = () => readFileSync(join(ROOT, 'app', 'privacy', 'page.tsx'), 'utf-8')

describe('privacy policy identifies the personal-data operator', () => {
  it('has an operator block with fillable placeholders, not silence', () => {
    const src = privacy()
    expect(src).toMatch(/Оператор персональных данных/)
    // The requisites must be present as clearly-marked placeholders (not invented real data).
    for (const field of ['ИНН', 'ОГРН', 'Адрес']) {
      expect(src).toMatch(new RegExp(`${field}[^\\n]*\\[УКАЗАТЬ`))
    }
    // A contact for data-subject requests is a real, working address.
    expect(src).toMatch(/hello@biznes-pult\.ru/)
  })

  it('does not present a placeholder as a real filled-in requisite', () => {
    // Guard against someone half-filling one field with a fake number: every requisite line that
    // names ИНН/ОГРН must still carry the [УКАЗАТЬ ...] marker until real details are entered.
    const src = privacy()
    expect(src).not.toMatch(/ИНН:\s*\d{10,12}\b/)          // no bare digit INN slipped in
    expect(src).not.toMatch(/ОГРН[А-Я]*:\s*\d{13,15}\b/)   // no bare digit OGRN slipped in
  })
})

describe('deletion wording matches the soft-delete implementation', () => {
  it('describes deactivation + retention, not an automatic hard delete', () => {
    const src = privacy()
    // Honest: account is deactivated and data is retained until an erasure request.
    expect(src).toMatch(/деактивируется/)
    expect(src).toMatch(/не стираются немедленно|сохраняются в деактивированном виде/)
    // Erasure is a manual request to a real address, within a stated window.
    expect(src).toMatch(/hello@biznes-pult\.ru/)
    expect(src).toMatch(/в течение 30 дней/)
  })

  it('no longer promises automatic deletion the backend does not perform', () => {
    const src = privacy()
    // The old copy said account data "удаляются в течение 30 дней" and financial data
    // "удаляются вместе с аккаунтом" unconditionally — the soft delete does neither.
    expect(src).not.toMatch(/удаляются вместе с аккаунтом/)
    expect(src).not.toMatch(/хэш пароля\) удаляются в течение 30 дней/)
  })
})

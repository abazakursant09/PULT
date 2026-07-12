import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

// Release-honesty guards, slice 3. Every switch below was read by ZERO backend code: a seller
// could turn it on and nothing on earth would ever send it. Each test fails the day one returns.
//
// The flags are asserted against the page sources rather than a render, because what must never
// come back is the *control* — no mock of a backend that does not consume it could prove that.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')
const code = (...p: string[]) => read(...p).replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')

const SETTINGS = ['app', 'dashboard', 'settings', 'page.tsx']
const ACCOUNT = ['app', 'dashboard', 'account', 'page.tsx']

/** Flags with zero consumers in the backend — verified by grep across backend/**\/*.py. */
const DEAD_FLAGS = [
  'notify_bad_review',
  'notify_offer_change',
  'notify_price_drop',
  'notify_negative_review',
  'notify_trial_end',
  'notify_weekly_report',
  'notify_ab_results',
  'notify_retention',
]

/** Flags the Intelligence Loop really reads (intelligence_loop.py:1402,1404,1406). */
const LIVE_FLAGS = ['notify_insights', 'notify_seo_opportunity', 'notify_sales_growth']

describe('no seller-facing switch is wired to nothing', () => {
  for (const page of [SETTINGS, ACCOUNT]) {
    const name = page[2]

    it(`${name} offers none of the dead notification switches`, () => {
      const src = code(...page)
      for (const flag of DEAD_FLAGS) {
        expect(src, `${flag} has no backend consumer and must not be offered`).not.toMatch(flag)
      }
    })
  }

  it('keeps the three switches that do work', () => {
    const src = code(...ACCOUNT)
    for (const flag of LIVE_FLAGS) expect(src).toMatch(flag)
  })

  it('offers no "критично" badge on Настройки — all three wearing it were dead', () => {
    expect(code(...SETTINGS)).not.toMatch(/критично/)
  })
})

describe('the false inactivity reminder does not return', () => {
  it('promises no reminder based on not logging in', () => {
    // The Intelligence Loop never reads last_login; retention_inactive_days was a send-cooldown,
    // so an actively working seller was told "Пока вас не было".
    for (const page of [SETTINGS, ACCOUNT]) {
      const src = code(...page)
      expect(src).not.toMatch(/не заходили/)
      expect(src).not.toMatch(/retention_inactive_days/)
    }
  })
})

describe('the API-key reminder does not return', () => {
  it('promises no expiry reminder, and advertises no key management', () => {
    // The date lived in localStorage, the 180-day lifetime was a client-side constant, and no
    // sender existed anywhere for the "напомним за 7 дней" promise.
    const src = code(...SETTINGS)

    expect(src).not.toMatch(/напомним/i)
    expect(src).not.toMatch(/180/)
    expect(src).not.toMatch(/ozon_api_key_date/)
    expect(src).not.toMatch(/API-ключ/)
  })
})

describe('the support action reaches a human', () => {
  it('does not send the seller to the localStorage-only ticket form', () => {
    // app/support/page.tsx writes the ticket to localStorage['bp_support_tickets'] and calls no
    // API. It was the only offered way to change a name or an email.
    const src = code(...ACCOUNT)

    expect(src).not.toMatch(/["']\/support["']/)
    expect(src).toMatch(/mailto:hello@biznes-pult\.ru/)
  })
})

describe('account recovery is described the same way on both pages', () => {
  it('invents no recovery window, and the two pages agree', () => {
    // routers/referrals.py soft-deletes with no expiry: there is no 30-day window to promise.
    const account = code(...ACCOUNT)
    const settings = code(...SETTINGS)

    for (const src of [account, settings]) {
      expect(src).not.toMatch(/30 дней/)
      expect(src).toMatch(/Email сохраняется и может быть\s+использован|Email сохраняется и может быть использован/)
      expect(src).toMatch(/восстановлением реферальной истории/)
    }
  })
})

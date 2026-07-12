/** The one place a user.plan value is turned into a Russian name.
 *
 * Billing and Account each kept their own map, and they disagreed: the same `master` seller
 * read "Старт" on Тариф and "Мастер" on Аккаунт, on adjacent pages. Billing's names are the
 * canonical ones — they are the names actually sold (TARIFFS) and the names printed on the
 * payment history rows — so those are the names kept here.
 *
 * These are exactly the identifiers the backend stores (models/user.py: master | profi |
 * maximum). Nothing here invents a plan, and an unknown value is never given a name that
 * implies it is paid or active — it falls back to a neutral dash.
 */
export const PLAN_LABELS: Record<string, string> = {
  master: 'Старт',
  profi: 'Мастер',
  maximum: 'Профи',
}

/** Shown when the plan is missing or is a value this frontend does not know. */
export const UNKNOWN_PLAN_LABEL = '—'

export function planLabel(plan?: string | null): string {
  if (!plan) return UNKNOWN_PLAN_LABEL
  return PLAN_LABELS[plan] ?? UNKNOWN_PLAN_LABEL
}

/** Has a payment ever activated this seller's plan?
 *
 * `plan` alone cannot answer that: the column defaults to 'master' for every account at
 * registration (models/user.py:18), so a seller who has never paid a kopeck still carries a
 * paid-looking plan value. `subscription_end_date` is the only unambiguous field — the backend
 * sets it when a payment activates the plan (models/user.py:45). Absent it, we say nothing
 * rather than guess.
 */
export function hasActiveSubscription(user?: { subscription_end_date?: string | null } | null): boolean {
  return !!user?.subscription_end_date
}

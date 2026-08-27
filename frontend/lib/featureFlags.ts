/**
 * ПУЛЬТ feature-flags.
 *
 * GROWTH-контур (referrals / deals / community / academy / ideas / market-overview)
 * заморожен в V2: код жив, из навигации убран. Включается флагом — БЕЗ переписывания.
 *
 * Включить: `NEXT_PUBLIC_GROWTH_CONTOUR=1` в .env(.local). По умолчанию выключен.
 *
 * Billing is a separate commercial gate. Frontend visibility never unlocks the backend:
 * `BILLING_ENABLED=1` must also be set server-side before any payment endpoint exists.
 */
export const FLAGS = {
  growthContour: process.env.NEXT_PUBLIC_GROWTH_CONTOUR === '1',
  billing: process.env.NEXT_PUBLIC_BILLING_ENABLED === '1',
} as const

export type FlagKey = keyof typeof FLAGS
export const isEnabled = (k: FlagKey): boolean => FLAGS[k]

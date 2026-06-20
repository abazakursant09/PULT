/**
 * ПУЛЬТ feature-flags.
 *
 * GROWTH-контур (referrals / deals / community / academy / ideas / market-overview)
 * заморожен в V2: код жив, из навигации убран. Включается флагом — БЕЗ переписывания.
 *
 * Включить: `NEXT_PUBLIC_GROWTH_CONTOUR=1` в .env(.local). По умолчанию выключен.
 */
export const FLAGS = {
  growthContour: process.env.NEXT_PUBLIC_GROWTH_CONTOUR === '1',
} as const

export type FlagKey = keyof typeof FLAGS
export const isEnabled = (k: FlagKey): boolean => FLAGS[k]

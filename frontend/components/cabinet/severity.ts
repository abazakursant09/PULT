import type { Severity } from '@/lib/pultProduct'

/**
 * Severity → CSS-vars. Единственное место маппинга цвета сигнала.
 * Канон: red=убыток, amber=риск, green=прибыль. Только var(), без хардкод-hex.
 */
export const SEV: Record<Severity, { color: string; dim: string; label: string }> = {
  red:   { color: 'var(--danger)',  dim: 'var(--danger-dim)',  label: 'убыток' },
  amber: { color: 'var(--warning)', dim: 'var(--warning-dim)', label: 'риск' },
  green: { color: 'var(--success)', dim: 'var(--success-dim)', label: 'рост' },
}

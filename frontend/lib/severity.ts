// Observed severity → (RU label, P1 Badge variant). Severity is a verbatim OBSERVED class from
// the diagnosis, not a score PULT computes here. This module only chooses how to *present* it:
// critical reads as danger, high as warning, everything else stays neutral — colour carries the
// urgency the old flat-gray pill hid. Unknown values fall back to neutral + the raw string.
import type { BadgeProps } from '@/components/ui/badge'

type Variant = NonNullable<BadgeProps['variant']>

const _LABEL: Record<string, string> = {
  critical: 'Критично', high: 'Высокий приоритет', medium: 'Средний приоритет', low: 'Низкий приоритет',
}
const _VARIANT: Record<string, Variant> = {
  critical: 'danger', high: 'warning', medium: 'neutral', low: 'neutral',
}

export function severityLabel(s: string | null): string | null {
  return s ? (_LABEL[s] ?? s) : null
}

export function severityBadgeVariant(s: string | null): Variant {
  return (s && _VARIANT[s]) || 'neutral'
}

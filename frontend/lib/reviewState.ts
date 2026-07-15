// Review lifecycle state → (RU label, P1 Badge variant). The state itself is derived by the
// backend (AR0–AR3) and carried verbatim on ReviewResponse.state; this module only chooses how to
// *present* it. Colour now carries the meaning the old flat per-state hex map buried: attention is
// warning, a failure is danger, an approved/published answer is success, a draft is the violet
// accent, and the quiet in-flight states stay neutral. Presentation only — no state is computed here.
import type { ReviewState } from '@/lib/api'
import type { BadgeProps } from '@/components/ui/badge'

type Variant = NonNullable<BadgeProps['variant']>

export const STATE_LABEL: Record<ReviewState, string> = {
  New: 'Новый', Processing: 'Обработка', Drafted: 'Черновик',
  NeedsAttention: 'Требует внимания', Approved: 'Одобрен', Published: 'Опубликован', Failed: 'Ошибка',
}

export const STATE_BADGE_VARIANT: Record<ReviewState, Variant> = {
  New: 'neutral', Processing: 'neutral', Drafted: 'default',
  NeedsAttention: 'warning', Approved: 'success', Published: 'success', Failed: 'danger',
}

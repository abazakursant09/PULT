'use client'
import { EmptyState } from '@/components/ui/EmptyState'

// Honest empty state — never "всё хорошо" / "проблем нет" / "бизнес в порядке". Says only that
// there is nothing to show yet, and why. Rendered through the canonical P1 EmptyState.
export function DecisionFeedEmptyState() {
  return (
    <EmptyState
      title="Пока нет решений"
      description="Когда появятся новые сигналы, PULT покажет решения здесь."
    />
  )
}

export default DecisionFeedEmptyState

'use client'

// Honest empty state — never "всё хорошо" / "проблем нет" / "бизнес в порядке".
export function DecisionFeedEmptyState() {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12,
      padding: '22px 18px', textAlign: 'center', color: 'var(--text-3)', fontSize: 13,
    }}>
      На сейчас нет новых решений. PULT продолжит следить за сигналами.
    </div>
  )
}

export default DecisionFeedEmptyState

'use client'

// Shared error state (retry). P4: migrated off the legacy `T` hex tokens onto the P0 CSS token
// system — styling only, the prop API is unchanged. Import is its sole consumer.
interface ErrorStateProps {
  message?:    string
  onRetry?:    () => void
  retryLabel?: string
  paddingTop?: number
}

export function ErrorState({
  message    = 'Данные временно недоступны',
  onRetry,
  retryLabel = 'Повторить',
  paddingTop = 64,
}: ErrorStateProps) {
  return (
    <div
      className="flex flex-col items-center gap-3 pb-12"
      style={{ paddingTop }}
    >
      <span className="text-[13px] text-[var(--text-3)]">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-[11px] text-[var(--violet-text)] bg-transparent border border-[var(--line)] rounded-[var(--r-sm)] px-3.5 py-1.5 cursor-pointer transition-[border-color,color] duration-[var(--dur)] hover:border-[var(--violet-text)]"
        >
          {retryLabel}
        </button>
      )}
    </div>
  )
}

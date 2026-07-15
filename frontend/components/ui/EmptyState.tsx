import * as React from 'react'
import { cn } from '@/lib/utils'

// Canonical PULT empty state (P1). Honest by construction — it shows only what the caller passes
// (a title, an optional line of context, an optional real action). It never fabricates numbers,
// rows, or placeholder content. Use it wherever a list/section has genuinely no data yet.
export interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Optional leading glyph/icon node. Purely decorative. */
  icon?: React.ReactNode
  /** Short, truthful headline — e.g. "Пока нет отзывов". */
  title: string
  /** Optional one line of honest context. No fake metrics. */
  description?: string
  /** Optional real call to action (a Button, a Link). Omit if there is nothing to do. */
  action?: React.ReactNode
}

export const EmptyState = React.forwardRef<HTMLDivElement, EmptyStateProps>(
  ({ className, icon, title, description, action, ...props }, ref) => (
    <div
      ref={ref}
      role="status"
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-[var(--r-sm)]',
        'border border-dashed border-[var(--line)] bg-[var(--surface)]',
        'px-6 py-12 text-center',
        className,
      )}
      {...props}
    >
      {icon && (
        <div className="text-[var(--text-3)] [&_svg]:h-8 [&_svg]:w-8" aria-hidden>
          {icon}
        </div>
      )}
      <p className="text-[15px] font-semibold text-[var(--text)]">{title}</p>
      {description && (
        <p className="max-w-[42ch] text-[13px] leading-relaxed text-[var(--text-2)]">{description}</p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
)
EmptyState.displayName = 'EmptyState'

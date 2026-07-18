// Shared parsing/formatting for backend timestamps.
//
// The API serializes naive UTC (`datetime.utcnow()` → "2026-07-18T15:40:00", no suffix). `new Date`
// reads a suffix-less datetime as LOCAL time, so rendering one directly is silently wrong by the
// browser's offset — no error, just the wrong hour. Everything that shows a PULT timestamp must go
// through here: append the missing Z, then let the browser do the timezone work. No manual offset
// arithmetic anywhere.

const TZ_SUFFIX = /(?:Z|[+-]\d{2}:?\d{2})$/i

/** Parse a backend timestamp as UTC. Returns null for anything unparseable — callers show a neutral
 *  fallback rather than crashing the page. */
export function parseUtc(raw: string | null | undefined): Date | null {
  if (raw == null) return null
  const s = String(raw).trim()
  if (!s) return null
  const d = new Date(TZ_SUFFIX.test(s) ? s : `${s}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

/** Local wall-clock, e.g. "15:40". */
export function formatTime(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** Local date + time, e.g. "18.07 15:40". */
export function formatDateTime(d: Date): string {
  const date = d.toLocaleDateString(undefined, { day: '2-digit', month: '2-digit' })
  return `${date} ${formatTime(d)}`
}

/** Time alone when the moment falls on today, date + time otherwise. A long backoff or an old
 *  attempt can land on another day, and a bare "04:00" would read as "in a few minutes". */
export function formatSmart(d: Date): string {
  const now = new Date()
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
  return sameDay ? formatTime(d) : formatDateTime(d)
}

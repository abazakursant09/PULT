// SECURITY-2D-1B-B — client operation key (idempotency identity) for manual executable writes.
//
// One canonical lowercase UUIDv4 is minted per user INTENT (one button press), held in component state,
// and sent as the `Idempotency-Key` header. The transport reuses the same `init` across retries, so the
// header — and therefore the operation identity — is stable across a 5xx/network retry. A rerender does
// NOT mint a new key; a separate deliberate click mints a new one (a genuinely new operation).
//
// The backend wraps this into `v1:client:<uuid>`; the client never sends the namespace prefix. The key
// is not authorization — the session cookie authenticates.

function fromRandomBytes(): string {
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  b[6] = (b[6] & 0x0f) | 0x40 // version 4
  b[8] = (b[8] & 0x3f) | 0x80 // variant 10
  const h = Array.from(b, x => x.toString(16).padStart(2, '0'))
  return (
    h.slice(0, 4).join('') + '-' + h.slice(4, 6).join('') + '-' + h.slice(6, 8).join('') +
    '-' + h.slice(8, 10).join('') + '-' + h.slice(10, 16).join('')
  )
}

/** Mint one canonical lowercase UUIDv4 for a single executable user intent. */
export function newOperationKey(): string {
  if (typeof crypto === 'undefined') {
    // No Web Crypto → an executable write cannot be made safely idempotent; fail loudly rather than
    // send a non-idempotent request the backend would reject.
    throw new Error('secure context required to start this action')
  }
  const uuid = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : fromRandomBytes()
  return uuid.toLowerCase()
}

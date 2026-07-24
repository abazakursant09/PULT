import { StorePicker } from '@/components/stores/StorePicker'

// The old page uploaded a file with only a marketplace, which the backend has refused since
// PULT-LAUNCH-1.4.2 — a CSV must name the store it lands in. Rather than keep a second CSV flow
// alive, this route became the step that was missing: choose the store, then continue into the
// one real flow at /dashboard/stores/[storeId]/import.

export default function ImportEntryPage() {
  return <StorePicker />
}

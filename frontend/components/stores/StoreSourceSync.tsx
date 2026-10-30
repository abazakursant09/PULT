'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { api } from '@/lib/api'

type Snapshot = { revision: number; busy: boolean; outcome: 'confirmed' | 'unknown' | null }
type StoreRequests = {
  snapshot: Snapshot
  listeners: Set<() => void>
  mutation: Promise<unknown> | null
  reads: Map<string, Promise<void>>
}
const initial: Snapshot = { revision: 0, busy: false, outcome: null }
// Transient coordination only: no financial values, preferences or credentials.
// Survives navigation during IO, then disappears when no view or request remains.
const stores = new Map<string, StoreRequests>()
function requests(storeId: string): StoreRequests {
  let entry = stores.get(storeId)
  if (!entry) {
    entry = { snapshot: initial, listeners: new Set(), mutation: null, reads: new Map() }
    stores.set(storeId, entry)
  }
  return entry
}
function release(storeId: string, entry: StoreRequests) {
  queueMicrotask(() => {
    if (!entry.listeners.size && !entry.mutation && !entry.reads.size && stores.get(storeId) === entry) stores.delete(storeId)
  })
}
function publish(entry: StoreRequests, snapshot: Snapshot) {
  entry.snapshot = snapshot
  entry.listeners.forEach(listener => listener())
}

export function useStoreSourceSync(storeId: string) {
  const subscribe = useCallback((listener: () => void) => {
    const entry = requests(storeId)
    entry.listeners.add(listener)
    return () => { entry.listeners.delete(listener); release(storeId, entry) }
  }, [storeId])
  const snapshot = useSyncExternalStore(subscribe, useCallback(() => requests(storeId).snapshot, [storeId]), () => initial)
  const change = useCallback(async (metric: string, preference: 'auto' | 'api' | 'csv') => {
    const entry = requests(storeId)
    if (entry.mutation) return
    const mutation = api.sourcePolicy.set(storeId, metric, preference)
    entry.mutation = mutation
    publish(entry, { revision: entry.snapshot.revision + 1, busy: true, outcome: null })
    let outcome: Snapshot['outcome'] = 'confirmed'
    try { await mutation } catch { outcome = 'unknown' }
    finally {
      // A lost response is not proof that the server rejected the choice.
      // Both views reconcile through GET; read retries never replay PATCH.
      entry.mutation = null
      publish(entry, { revision: entry.snapshot.revision + 1, busy: false, outcome })
      release(storeId, entry)
    }
  }, [storeId])
  return { ...snapshot, change }
}

// A fresh read waits for older same-endpoint IO (including abandoned mounts),
// otherwise req() would deduplicate it onto a pre-write response. No global
// transport changes or client-side financial calculations are needed.
export function useConfirmedStoreRead<T>(storeId: string, revision: number, kind: 'finance' | 'policy', request: () => Promise<T>) {
  const key = `${storeId}:${revision}`
  const [result, setResult] = useState<{ key: string; state: 'loading' | 'ready' | 'failed'; data: T | null }>({
    key, state: 'loading', data: null,
  })
  const generation = useRef(0)
  const reload = useCallback(async () => {
    const current = ++generation.current
    const entry = requests(storeId)
    const previous = entry.reads.get(kind)
    setResult({ key, state: 'loading', data: null })
    const valid = () => current === generation.current && entry.snapshot.revision === revision
    const task = (async () => {
      await previous
      await entry.mutation?.catch(() => undefined)
      if (!valid()) return
      try {
        const data = await request()
        if (valid()) setResult({ key, state: 'ready', data })
      } catch {
        if (valid()) setResult({ key, state: 'failed', data: null })
      }
    })()
    entry.reads.set(kind, task)
    await task
    if (entry.reads.get(kind) === task) entry.reads.delete(kind)
    release(storeId, entry)
  }, [key, storeId, revision, kind, request])

  useEffect(() => {
    void reload()
    return () => { generation.current += 1 }
  }, [reload])

  return { state: result.key === key ? result.state : 'loading', data: result.key === key ? result.data : null, reload }
}

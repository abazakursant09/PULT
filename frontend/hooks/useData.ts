'use client'
/**
 * Stale-while-revalidate hook.
 *
 * On mount:
 *   1. Synchronously applies any cached value → zero loading flash on warm cache
 *   2. If cache is stale (or empty), fires background fetch
 *   3. Updates silently — no loading spinner during revalidation
 *
 * AbortErrors are swallowed (navigation-cancels are invisible to users).
 * All other errors are swallowed too — stale data stays visible.
 */
import { useState, useEffect, useRef } from 'react'

interface Entry<T> { data: T; ts: number }

const _store = new Map<string, Entry<unknown>>()

export function getStored<T>(key: string): T | null {
  return (_store.get(key)?.data ?? null) as T | null
}

export function useData<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs = 30_000,
): { data: T | null; refreshing: boolean } {
  const [data,       setData]       = useState<T | null>(() => {
    const entry = _store.get(key) as Entry<T> | undefined
    return entry?.data ?? null
  })
  const [refreshing, setRefreshing] = useState(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    const entry = _store.get(key) as Entry<T> | undefined

    if (entry) {
      setData(entry.data)                             // instant: no loading flash
      if (Date.now() - entry.ts < ttlMs) return       // fresh: skip network
    }

    let cancelled = false
    setRefreshing(true)

    fetcher()
      .then(fresh => {
        if (cancelled || !mounted.current) return
        _store.set(key, { data: fresh, ts: Date.now() })
        setData(fresh)
      })
      .catch(() => {})                                // keep showing stale on error
      .finally(() => {
        if (!cancelled && mounted.current) setRefreshing(false)
      })

    return () => { cancelled = true }
  // key is stable per call-site; fetcher identity is intentionally ignored
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ttlMs])

  return { data, refreshing }
}

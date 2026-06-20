'use client'
import { useEffect, useState } from 'react'
import { getProducts, type Mode, type ProductWithLenses } from '@/lib/pultProduct'

const KEY = 'pult_cabinet_mode'

/**
 * Режим данных вкладки (real / demo / empty) + товары из селектора.
 * Режим хранится в localStorage → переключатель состояний общий на весь Кабинет.
 * TODO ingest: when real ingest lands, derive mode from api `has_data`.
 */
export function useCabinet(): {
  mode: Mode; setMode: (m: Mode) => void; products: ProductWithLenses[]
} {
  const [mode, setModeState] = useState<Mode>('demo')

  useEffect(() => {
    try {
      const m = localStorage.getItem(KEY) as Mode | null
      if (m === 'real' || m === 'demo' || m === 'empty') setModeState(m)
    } catch { /* ignore */ }
  }, [])

  function setMode(m: Mode) {
    setModeState(m)
    try { localStorage.setItem(KEY, m) } catch { /* ignore */ }
  }

  return { mode, setMode, products: getProducts(mode) }
}

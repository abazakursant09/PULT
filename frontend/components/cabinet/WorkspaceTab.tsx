'use client'
import type { Mode } from '@/lib/pultProduct'

/** StateBanner — честность данных (demo / csv). real/empty не показывают баннер. */
export function StateBanner({ mode }: { mode: Mode }) {
  if (mode === 'demo') {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16,
        background: 'var(--warning-dim)', border: '1px solid var(--warning)',
        borderRadius: 10, padding: '9px 13px',
      }}>
        <span style={{ fontSize: 13 }}>🧪</span>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--warning)' }}>
          Демо-режим — пример решений, не ваши данные.
        </span>
      </div>
    )
  }
  return null
}

/** Переключатель режима данных (Desktop topbar и mobile). */
export function StateSwitch({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  const opt: Mode[] = ['real', 'demo', 'empty']
  const lbl: Record<Mode, string> = { real: 'Real', demo: 'Demo', empty: 'Empty' }
  return (
    <div style={{ display: 'inline-flex', background: 'var(--surface-h)', border: '1px solid var(--line)', borderRadius: 9, padding: 3 }}>
      {opt.map(o => (
        <button key={o} onClick={() => onChange(o)} style={{
          border: 0, cursor: 'pointer', padding: '5px 11px', borderRadius: 7, fontSize: 12, fontWeight: 600,
          background: mode === o ? 'var(--violet)' : 'transparent',
          color: mode === o ? '#fff' : 'var(--text-3)',
        }}>{lbl[o]}</button>
      ))}
    </div>
  )
}

/**
 * WorkspaceTab — единый каркас каждой вкладки Кабинета.
 * Header → StateSwitch → StateBanner → summary slot → body.
 * Инвариант: ничего, кроме этого порядка. Ни таблиц-стен, ни графиков ради графиков.
 */
export function WorkspaceTab({
  title, subtitle, mode, onMode, summary, children,
}: {
  title: string; subtitle: string; mode: Mode; onMode: (m: Mode) => void
  summary?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <div style={{ background: 'var(--bg)', minHeight: '100vh', padding: '26px 24px 80px', maxWidth: 1020, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 18, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: '-0.01em', color: 'var(--text)' }}>{title}</h1>
          <div style={{ fontSize: 13, color: 'var(--text-3)', marginTop: 3 }}>{subtitle}</div>
        </div>
        <StateSwitch mode={mode} onChange={onMode} />
      </div>
      <StateBanner mode={mode} />
      {summary}
      {children}
    </div>
  )
}

/** Пустое состояние — онбординг-импорт, не фейковые числа. */
export function EmptyCabinet({ hint }: { hint?: string }) {
  return (
    <div style={{
      border: '1px dashed var(--line)', borderRadius: 14, padding: 34, textAlign: 'center',
      color: 'var(--text-3)', fontSize: 13,
    }}>
      <div style={{ fontSize: 26, marginBottom: 8 }}>📦</div>
      <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>Данных по товарам пока нет</div>
      <div style={{ maxWidth: 420, margin: '0 auto 16px' }}>
        {hint ?? 'Загрузите выгрузку WB/Ozon — ПУЛЬТ покажет проблемы и решения по каждому товару.'}
      </div>
      <a href="/dashboard/settings" style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13.5, fontWeight: 700,
        padding: '11px 20px', borderRadius: 9, textDecoration: 'none',
        background: 'var(--violet)', color: '#fff',
      }}>Загрузить данные →</a>
    </div>
  )
}

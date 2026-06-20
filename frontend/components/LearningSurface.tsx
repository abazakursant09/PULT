'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { LearningAlternativesResponse, DecisionEvidence } from '@/lib/api'

/**
 * LearningSurface — read-only вывод обучения PULT по одному insight:
 * рекомендованное действие + причина + альтернативы (ранжированы по памяти исходов).
 * Только чтение: НЕТ кнопок исполнения/одобрения, НЕТ мутаций. Контекст берётся
 * консистентно — один и тот же listing_id передаётся в оба вызова (E4).
 */
export interface LearningSurfaceProps {
  insightKey: string
  listingId?: string | number | null
}

const TXT_EMPTY    = 'Недостаточно истории для рекомендаций.'
const TXT_FALLBACK = 'Используется порядок действий по умолчанию.'
const TXT_DEGRADED = 'Рекомендация построена на неполном контексте.'

function pct(v: number | null): string | null {
  return v == null ? null : `${Math.round(v * 100)}%`
}

function Banner({ tone, children }: { tone: 'warn' | 'muted'; children: React.ReactNode }) {
  const warn = tone === 'warn'
  return (
    <div style={{
      fontSize: 11.5, padding: '8px 12px', borderRadius: 8, marginBottom: 10,
      background: warn ? 'var(--surface-h)' : 'var(--surface)',
      border: '1px solid var(--line)',
      color: warn ? 'var(--text)' : 'var(--text-3)',
    }}>{children}</div>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 14, padding: 18 }}>
      {children}
    </div>
  )
}

export function LearningSurface({ insightKey, listingId }: LearningSurfaceProps) {
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [data, setData]         = useState<LearningAlternativesResponse | null>(null)
  const [evidence, setEvidence] = useState<DecisionEvidence | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null); setEvidence(null)
    const listing_id = listingId != null ? String(listingId) : undefined
    ;(async () => {
      try {
        const alts = await api.learning.getLearningAlternatives({ insight_key: insightKey, listing_id })
        if (!alive) return
        setData(alts)
        // Доказательство грузим только для верхнего действия (тем же listing_id).
        if (alts.alternatives.length > 0) {
          const ev = await api.learning.getDecisionEvidence({
            insight_key: insightKey, action_key: alts.alternatives[0].action_key, listing_id,
          })
          if (alive) setEvidence(ev.evidence)
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : 'Ошибка загрузки')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [insightKey, listingId])

  // ── Loading ────────────────────────────────────────────────────────────────
  if (loading) {
    return <Shell><div style={{ fontSize: 12.5, color: 'var(--text-3)' }}>Загрузка рекомендаций…</div></Shell>
  }

  // ── Error ──────────────────────────────────────────────────────────────────
  if (error) {
    return <Shell><div style={{ fontSize: 12.5, color: 'var(--danger)' }}>Не удалось загрузить: {error}</div></Shell>
  }

  // ── Empty (нет альтернатив для этого insight) ───────────────────────────────
  if (!data || data.alternatives.length === 0) {
    return (
      <Shell>
        <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '8px 0' }}>{TXT_EMPTY}</div>
      </Shell>
    )
  }

  const top = data.alternatives[0]
  const rest = data.alternatives.slice(1)
  const w = pct(top.weighted_rate)

  return (
    <Shell>
      {data.degraded && <Banner tone="warn">{TXT_DEGRADED}</Banner>}
      {top.fallback  && <Banner tone="muted">{TXT_FALLBACK}</Banner>}

      {/* ── Рекомендованное действие ───────────────────────────────────────── */}
      <div style={{ marginBottom: rest.length ? 14 : 0 }}>
        <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, color: 'var(--text-3)', marginBottom: 6 }}>
          Рекомендуемое действие
        </div>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>{top.action_key}</div>
        <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 4 }}>{(evidence?.reason ?? top.reason)}</div>
        <div style={{ display: 'flex', gap: 14, marginTop: 8, fontSize: 11, color: 'var(--text-3)' }}>
          <span>Наблюдений: {top.sample}</span>
          {w && <span>Вес недавних исходов: {w}</span>}
        </div>
      </div>

      {/* ── Альтернативы ────────────────────────────────────────────────────── */}
      {rest.length > 0 && (
        <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, color: 'var(--text-3)', marginBottom: 8 }}>
            Альтернативы
          </div>
          {rest.map((a) => (
            <div key={a.action_key} style={{ display: 'flex', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', flex: '0 0 auto', minWidth: 18 }}>#{a.rank}</span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12.5, color: 'var(--text)' }}>{a.action_key}</div>
                <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{a.reason}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Shell>
  )
}

export default LearningSurface

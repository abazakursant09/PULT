'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { RefreshCw, Upload, TrendingUp, AlertTriangle, CheckCircle, Eye, ChevronRight } from 'lucide-react'
import {
  api,
  type InsightsResponse,
  type InsightItem,
  type InsightAction,
  type OperationalScenario,
  type PortfolioPattern,
  type OperationalSummary,
  type StrategyCommitment,
  type OperationalRegime,
  type DecisionEnergy,
  type OperationalPhaseTransition,
  type StabilityTopology,
  type OperationalDoctrine,
  type InstitutionalInertia,
  type StructuralRecoveryCapacity,
} from '@/lib/api'
import { trackEvent } from '@/lib/events'
import { T } from '@/lib/tokens'
import { ErrorState } from '@/components/system/ErrorState'
import { EmptyState } from '@/components/system/EmptyState'
import { SkeletonList } from '@/components/system/SkeletonList'

// ── Local aliases keep diffs minimal ─────────────────────────────────────────
const R = {
  bg:    T.bgPage,
  surf:  T.surf,
  surfH: T.surfH,
  text:  T.text,
  text2: T.text2,
  text3: T.text3,
  v:     T.v,
  vDim:  T.vDim,
  line:  T.line,
  warn:  T.warn,
  warnD: T.warnD,
  ok:    T.ok,
  okD:   T.okD,
  red:   T.red,
  redD:  T.redD,
}

type Tab = 'all' | 'warnings' | 'positive' | 'resolved'

// ── Sub-components ─────────────────────────────────────────────────────────────

const AUTOMATION_META: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  safe_auto:      { label: 'Авто-безопасно',  color: '#22C55E', bg: 'rgba(34,197,94,0.10)',   icon: '✓' },
  human_required: { label: 'Нужно решение',   color: '#F59E0B', bg: 'rgba(245,158,11,0.10)',  icon: '!' },
  blocked:        { label: 'Заблокировано',   color: '#EF4444', bg: 'rgba(239,68,68,0.10)',   icon: '✕' },
  delayed:        { label: 'Ждём данные',     color: '#A78BFA', bg: 'rgba(167,139,250,0.10)', icon: '⏱' },
  critical_alert: { label: 'Критично',        color: '#EF4444', bg: 'rgba(239,68,68,0.14)',   icon: '⚡' },
}

function AutomationBadge({ level }: { level: string | null }) {
  if (!level) return null
  const m = AUTOMATION_META[level] ?? AUTOMATION_META.human_required
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
      padding: '2px 7px', borderRadius: 4,
      background: m.bg, color: m.color,
      whiteSpace: 'nowrap', flexShrink: 0,
      display: 'inline-flex', alignItems: 'center', gap: 4,
    }}>
      <span style={{ fontSize: 9 }}>{m.icon}</span>
      {m.label.toUpperCase()}
    </span>
  )
}

function ConfidencePill({ level, value }: { level: string; value: number }) {
  const colors: Record<string, { bg: string; text: string }> = {
    high:   { bg: 'rgba(34,197,94,0.10)',    text: '#22C55E' },
    medium: { bg: 'rgba(245,158,11,0.10)',   text: '#F59E0B' },
    low:    { bg: 'rgba(110,106,252,0.10)',  text: '#A78BFA' },
  }
  const c = colors[level] ?? colors.low
  return (
    <span style={{
      fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em',
      padding: '3px 8px', borderRadius: 20,
      background: c.bg, color: c.text,
      whiteSpace: 'nowrap',
    }}>
      Уверенность {value}%
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    active:     { label: 'Активно',     color: R.warn,  bg: R.warnD },
    monitoring: { label: 'Наблюдение',  color: '#A78BFA', bg: R.vDim },
    resolved:   { label: 'Решено',      color: R.ok,    bg: R.okD },
    dismissed:  { label: 'Скрыто',      color: R.text3, bg: 'rgba(255,255,255,0.05)' },
  }
  const s = map[status] ?? map.active
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
      padding: '2px 7px', borderRadius: 4,
      background: s.bg, color: s.color,
      whiteSpace: 'nowrap', flexShrink: 0,
    }}>
      {s.label.toUpperCase()}
    </span>
  )
}

function InsightCard({
  insight,
  onStatus,
  onAction,
  updating,
  deferCategories = [],
  capacityState   = 'stable',
}: {
  insight: InsightItem
  onStatus: (key: string, status: string) => void
  onAction: (action: InsightAction) => void
  updating: string | null
  deferCategories?: string[]
  capacityState?:   string
}) {
  const [expanded, setExpanded] = useState(true)

  const typeStyle: Record<string, { borderColor: string; iconBg: string }> = {
    warning:  { borderColor: R.warn, iconBg: R.warnD },
    positive: { borderColor: R.ok,   iconBg: R.okD   },
    info:     { borderColor: R.v,    iconBg: R.vDim  },
  }
  const ts = typeStyle[insight.type] ?? typeStyle.info
  const isResolved  = insight.status === 'resolved' || insight.status === 'dismissed'
  const isSecondary = insight.is_secondary
  const isBusy = updating === insight.key

  return (
    <div style={{
      background: R.surf,
      border:     `1px solid ${R.line}`,
      borderLeft: `3px solid ${ts.borderColor}`,
      borderRadius: 10,
      opacity: isResolved ? 0.55 : isSecondary ? 0.72 : 1,
      transition: 'opacity 0.2s',
    }}>
      {/* Header */}
      <div
        style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, padding: '14px 16px', cursor: 'pointer' }}
        onClick={() => {
          setExpanded(v => {
            if (!v) trackEvent('insight_opened', 'action_engine', insight.key, { insight_type: insight.type })
            return !v
          })
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flex: 1, minWidth: 0 }}>
          <span style={{
            fontSize: 20, lineHeight: 1, flexShrink: 0,
            background: ts.iconBg, borderRadius: 8,
            padding: '4px 6px', display: 'inline-flex',
          }}>{insight.icon}</span>
          <div style={{ minWidth: 0 }}>
            <p style={{ fontSize: 14, fontWeight: 600, color: R.text, lineHeight: 1.35, marginBottom: 2 }}>
              {insight.title}
            </p>
            {insight.subtitle && (
              <p style={{ fontSize: 12, color: R.text3, lineHeight: 1.3 }}>{insight.subtitle}</p>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexShrink: 0 }}>
          {isSecondary && (
            <span style={{
              fontSize: 9, fontWeight: 800, letterSpacing: '0.08em',
              padding: '2px 6px', borderRadius: 4,
              background: 'rgba(82,82,91,0.15)', color: '#71717A',
              whiteSpace: 'nowrap',
            }}>СЛЕДСТВИЕ</span>
          )}
          <AutomationBadge level={insight.automation_level} />
          <StatusBadge status={insight.status} />
          <ChevronRight size={12} style={{
            color: R.text3, transition: 'transform 0.15s',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
          }} />
        </div>
      </div>

      {/* Decision confidence row — always visible, Sprint 23 */}
      {insight.decision_confidence_score != null && insight.decision_confidence_band && (() => {
        const BAND_COLORS: Record<string, { text: string; bg: string }> = {
          low:      { text: '#F87171', bg: 'rgba(248,113,113,0.08)' },
          moderate: { text: '#F59E0B', bg: 'rgba(245,158,11,0.08)'  },
          stable:   { text: '#A78BFA', bg: 'rgba(167,139,250,0.08)' },
          high:     { text: '#34D399', bg: 'rgba(52,211,153,0.08)'  },
        }
        const BAND_LABELS: Record<string, string> = {
          low: 'низкая', moderate: 'умеренная', stable: 'устойчивая', high: 'высокая',
        }
        const c = BAND_COLORS[insight.decision_confidence_band] ?? BAND_COLORS.moderate
        return (
          <div style={{
            padding: '5px 16px 6px',
            borderTop: `1px solid ${R.line}`,
            background: c.bg,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: c.text, textTransform: 'uppercase' }}>
              Operational certainty
            </span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: c.text }}>
              {insight.decision_confidence_score}
            </span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 10, color: c.text, fontStyle: 'italic' }}>
              {BAND_LABELS[insight.decision_confidence_band]}
            </span>
          </div>
        )
      })()}

      {/* Signal lifecycle row — Sprint 24, always visible */}
      {insight.signal_lifecycle_stage && (() => {
        const LC_COLORS: Record<string, { text: string; bg: string }> = {
          emerging:   { text: '#A78BFA', bg: 'rgba(167,139,250,0.06)' },
          confirmed:  { text: '#F59E0B', bg: 'rgba(245,158,11,0.06)'  },
          stabilized: { text: '#34D399', bg: 'rgba(52,211,153,0.06)'  },
          recurring:  { text: '#F87171', bg: 'rgba(248,113,113,0.07)' },
          resolved:   { text: '#71717A', bg: 'rgba(113,113,122,0.06)' },
        }
        const c = LC_COLORS[insight.signal_lifecycle_stage] ?? LC_COLORS.emerging
        return (
          <div style={{
            padding: '5px 16px 6px',
            borderTop: `1px solid ${R.line}`,
            background: c.bg,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: c.text, textTransform: 'uppercase' }}>
              Lifecycle
            </span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: c.text }}>
              {insight.signal_lifecycle_stage}
            </span>
            {(insight.signal_operational_age ?? 0) > 0 && (
              <>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
                <span style={{ fontSize: 10, color: c.text, opacity: 0.7 }}>
                  {insight.signal_operational_age} дн.
                </span>
              </>
            )}
          </div>
        )
      })()}

      {/* Signal freshness row — Sprint 27, under Lifecycle */}
      {insight.signal_decay_state && insight.signal_decay_state !== 'fresh' && (() => {
        const DC_COLORS: Record<string, { text: string; bg: string }> = {
          aging:      { text: '#A78BFA', bg: 'rgba(167,139,250,0.05)' },
          fading:     { text: '#F59E0B', bg: 'rgba(245,158,11,0.05)'  },
          stale:      { text: '#71717A', bg: 'rgba(113,113,122,0.05)' },
          persistent: { text: '#F87171', bg: 'rgba(248,113,113,0.05)' },
        }
        const c = DC_COLORS[insight.signal_decay_state] ?? DC_COLORS.aging
        return (
          <div style={{
            padding: '4px 16px 5px',
            borderTop: `1px solid ${R.line}`,
            background: c.bg,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: R.text3, textTransform: 'uppercase' }}>
              Freshness
            </span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: c.text }}>
              {insight.signal_decay_state}
            </span>
            {(insight.signal_age_days ?? 0) > 0 && (
              <>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
                <span style={{ fontSize: 10, color: c.text, opacity: 0.65 }}>
                  {insight.signal_age_days} дн.
                </span>
              </>
            )}
          </div>
        )
      })()}

      {/* Trajectory row — Sprint 33 */}
      {insight.trajectory_state && (() => {
        const TRAJ_COLORS: Record<string, { text: string; bg: string }> = {
          reversible:               { text: '#34D399', bg: 'rgba(52,211,153,0.05)'   },
          stabilizing:              { text: '#A78BFA', bg: 'rgba(167,139,250,0.05)' },
          persistent:               { text: '#F59E0B', bg: 'rgba(245,158,11,0.05)'  },
          escalating:               { text: '#F87171', bg: 'rgba(248,113,113,0.06)' },
          structurally_accumulating: { text: '#EF4444', bg: 'rgba(239,68,68,0.06)' },
        }
        const TRAJ_LABELS: Record<string, string> = {
          reversible:               'обратимо',
          stabilizing:              'стабилизируется',
          persistent:               'давление накапливается',
          escalating:               'давление усиливается',
          structurally_accumulating: 'структурная нестабильность',
        }
        const c = TRAJ_COLORS[insight.trajectory_state] ?? { text: '#71717A', bg: 'transparent' }
        return (
          <div style={{
            padding: '5px 16px 6px',
            borderTop: `1px solid ${R.line}`,
            background: c.bg,
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: c.text, textTransform: 'uppercase' }}>
              Trajectory
            </span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: c.text }}>
              {TRAJ_LABELS[insight.trajectory_state] ?? insight.trajectory_state}
            </span>
          </div>
        )
      })()}

      {expanded && (
        <div style={{ padding: '0 16px 14px' }}>
          {/* Execution sequencing note — Sprint 32 */}
          {insight.sequence_stage != null && insight.stabilization_role && (() => {
            const _seqLabel: Record<string, string> = {
              fast_stabilization: 'Обычно выполняется на раннем этапе стабилизации.',
              structural_fix:     'Имеет смысл после снижения операционной волатильности.',
              parallel_track:     'Может быть реализовано параллельно с основной стабилизацией.',
              isolated:           'Независимый сигнал — не блокирует другие действия.',
            }
            const note = _seqLabel[insight.stabilization_role] ?? null
            if (!note) return null
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${insight.sequence_stage === 1 ? 'rgba(110,106,252,0.20)' : 'rgba(113,113,122,0.16)'}`,
              }}>
                <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                  ↳ {note}
                </span>
              </div>
            )
          })()}

          {/* Trajectory note — Sprint 33 */}
          {insight.trajectory_note && insight.trajectory_state && insight.trajectory_state !== 'stabilizing' && (() => {
            const TRAJ_BORDER: Record<string, string> = {
              reversible:               'rgba(52,211,153,0.20)',
              persistent:               'rgba(245,158,11,0.20)',
              escalating:               'rgba(248,113,113,0.24)',
              structurally_accumulating: 'rgba(239,68,68,0.24)',
            }
            const border = TRAJ_BORDER[insight.trajectory_state] ?? 'rgba(255,255,255,0.08)'
            const _windowLabel = (days: number) => {
              if (days <= 7)  return `${days} дней`
              if (days <= 14) return '1–2 недели'
              if (days <= 30) return '2–4 недели'
              if (days <= 45) return '3–6 недель'
              return '1–3 месяца'
            }
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${border}`,
              }}>
                <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                  ↳ {insight.trajectory_note}
                </span>
                {insight.stabilization_window_days && (
                  <div style={{ marginTop: 4, fontSize: 10.5, color: R.text3, opacity: 0.65 }}>
                    ≈ мягкая стабилизация: {_windowLabel(insight.stabilization_window_days)}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Tradeoff note — Sprint 34 */}
          {insight.tradeoff_note && (() => {
            const SEVERITY_BORDER: Record<string, string> = {
              mild:        'rgba(245,158,11,0.18)',
              moderate:    'rgba(248,113,113,0.20)',
              significant: 'rgba(239,68,68,0.28)',
            }
            const border = SEVERITY_BORDER[insight.tradeoff_severity ?? ''] ?? 'rgba(245,158,11,0.18)'
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.07em', color: 'rgba(245,158,11,0.65)', textTransform: 'uppercase' }}>
                    Временный эффект
                  </span>
                  {insight.tradeoff_duration_days && (
                    <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.25)' }}>
                      · ≈ {insight.tradeoff_duration_days}д
                    </span>
                  )}
                  {insight.reversibility_profile === 'reversible' && (
                    <span style={{ fontSize: 9.5, color: 'rgba(52,211,153,0.55)' }}>· обратимо</span>
                  )}
                </div>
                <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                  {insight.tradeoff_note}
                </span>
                {insight.stabilization_benefit && (
                  <div style={{ marginTop: 4, fontSize: 10.5, color: 'rgba(110,106,252,0.75)', lineHeight: 1.4 }}>
                    {insight.stabilization_benefit}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Forecast block — Sprint 35 */}
          {insight.forecast_fragility_state && insight.forecast_fragility_state !== 'stable' && (() => {
            const FRAG_COLOR: Record<string, { border: string; label: string; text: string }> = {
              sensitive: { border: 'rgba(110,106,252,0.22)', label: 'sensitive', text: 'rgba(167,139,250,0.70)' },
              fragile:   { border: 'rgba(245,158,11,0.22)',  label: 'fragile',   text: 'rgba(245,158,11,0.70)'  },
              critical:  { border: 'rgba(248,113,113,0.26)', label: 'critical',  text: 'rgba(248,113,113,0.75)' },
            }
            const c = FRAG_COLOR[insight.forecast_fragility_state] ?? FRAG_COLOR.sensitive
            const NEXT_STAGE_LABELS: Record<string, string> = {
              margin_crisis:                  'кризис маржи',
              advertising_dependency:         'структурная зависимость от рекламы',
              structural_margin_compression:  'структурная компрессия маржи',
              ranking_loss:                   'потеря видимости в поиске',
              inventory_pressure:             'давление на складские запасы',
            }
            const _wLabel = (days: number) => {
              if (days <= 7)  return `${days} дней`
              if (days <= 14) return '1–2 недели'
              if (days <= 21) return '2–3 недели'
              if (days <= 30) return '3–4 недели'
              return `≈ ${Math.round(days / 7)} недели`
            }
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${c.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.07em', color: c.text, textTransform: 'uppercase' }}>
                    Forecast
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.20)' }}>·</span>
                  <span style={{ fontSize: 9.5, fontWeight: 600, color: c.text }}>
                    {c.label}
                  </span>
                  {insight.forecast_instability_window_days && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.20)' }}>·</span>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.35)' }}>
                        ≈ {_wLabel(insight.forecast_instability_window_days)}
                      </span>
                    </>
                  )}
                  {insight.forecast_escalation_probability != null && (
                    <span style={{ marginLeft: 'auto', fontSize: 9.5, color: 'rgba(255,255,255,0.22)', fontVariantNumeric: 'tabular-nums' }}>
                      {insight.forecast_escalation_probability}%
                    </span>
                  )}
                </div>
                {insight.forecast_note && (
                  <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                    ↳ {insight.forecast_note}
                  </span>
                )}
                {insight.forecast_next_stage && NEXT_STAGE_LABELS[insight.forecast_next_stage] && (
                  <div style={{ marginTop: 5, fontSize: 10.5, color: 'rgba(255,255,255,0.22)', lineHeight: 1.4 }}>
                    Вероятная следующая фаза:{' '}
                    <span style={{ color: c.text }}>
                      {NEXT_STAGE_LABELS[insight.forecast_next_stage]}
                    </span>
                  </div>
                )}
                {insight.forecast_first_failure_mode && (
                  <div style={{ marginTop: 3, fontSize: 10.5, color: 'rgba(255,255,255,0.20)', lineHeight: 1.4 }}>
                    Первый сбой:{' '}
                    <span style={{ color: 'rgba(255,255,255,0.40)' }}>
                      {insight.forecast_first_failure_mode.toLowerCase()}
                    </span>
                  </div>
                )}
              </div>
            )
          })()}

          {/* Recovery block — Sprint 36 */}
          {insight.recovery_state && (() => {
            const REC_COLOR: Record<string, { border: string; text: string }> = {
              quick:      { border: 'rgba(52,211,153,0.20)',   text: 'rgba(52,211,153,0.65)'   },
              gradual:    { border: 'rgba(110,106,252,0.20)',  text: 'rgba(167,139,250,0.65)'  },
              unstable:   { border: 'rgba(245,158,11,0.20)',   text: 'rgba(245,158,11,0.65)'   },
              structural: { border: 'rgba(248,113,113,0.22)',  text: 'rgba(248,113,113,0.65)'  },
            }
            const c = REC_COLOR[insight.recovery_state] ?? REC_COLOR.gradual
            const _wLabel = (days: number) => {
              if (days <= 7)  return `${days} дней`
              if (days <= 14) return '1–2 недели'
              if (days <= 21) return '2–3 недели'
              if (days <= 30) return '3–4 недели'
              return `≈ ${Math.round(days / 7)} недели`
            }
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${c.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.07em', color: c.text, textTransform: 'uppercase' }}>
                    Recovery
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.20)' }}>·</span>
                  <span style={{ fontSize: 9.5, fontWeight: 600, color: c.text }}>
                    {insight.recovery_state}
                  </span>
                  {insight.expected_recovery_window_days && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.20)' }}>·</span>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.35)' }}>
                        ≈ {_wLabel(insight.expected_recovery_window_days)}
                      </span>
                    </>
                  )}
                  {insight.recovery_probability != null && (
                    <span style={{ marginLeft: 'auto', fontSize: 9.5, color: 'rgba(255,255,255,0.22)', fontVariantNumeric: 'tabular-nums' }}>
                      {insight.recovery_probability}%
                    </span>
                  )}
                </div>
                {insight.recovery_note && (
                  <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                    ↳ {insight.recovery_note}
                  </span>
                )}
                {(insight.first_recovered_metric || insight.lagging_metric) && (
                  <div style={{ marginTop: 5, display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.first_recovered_metric && (
                      <div style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.25)', lineHeight: 1.4 }}>
                        Сначала восстанавливается:{' '}
                        <span style={{ color: 'rgba(52,211,153,0.55)' }}>
                          {insight.first_recovered_metric}
                        </span>
                      </div>
                    )}
                    {insight.lagging_metric && (
                      <div style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.20)', lineHeight: 1.4 }}>
                        Дольше всего стабилизируется:{' '}
                        <span style={{ color: 'rgba(255,255,255,0.35)' }}>
                          {insight.lagging_metric}
                        </span>
                      </div>
                    )}
                  </div>
                )}
                {insight.recovery_dependency && (
                  <div style={{ marginTop: 4, fontSize: 10.5, color: 'rgba(255,255,255,0.18)', lineHeight: 1.4 }}>
                    Требует:{' '}
                    <span style={{ color: 'rgba(255,255,255,0.30)' }}>
                      {insight.recovery_dependency}
                    </span>
                  </div>
                )}
              </div>
            )
          })()}

          {/* Counterfactual pressure — Sprint 39 */}
          {insight.counterfactual_pressure_state && insight.counterfactual_pressure_state !== 'stable' && (() => {
            const CF_COLOR: Record<string, { border: string; text: string }> = {
              narrowing:          { border: 'rgba(110,106,252,0.20)', text: 'rgba(167,139,250,0.65)' },
              accelerating:       { border: 'rgba(245,158,11,0.22)',  text: 'rgba(245,158,11,0.70)'  },
              structurally_locked:{ border: 'rgba(248,113,113,0.22)', text: 'rgba(248,113,113,0.70)' },
            }
            const c = CF_COLOR[insight.counterfactual_pressure_state] ?? CF_COLOR.narrowing
            const _wLabel = (d: number) => {
              if (d <= 7)  return `${d} дней`
              if (d <= 14) return '1–2 недели'
              if (d <= 21) return '2–3 недели'
              if (d <= 30) return '3–4 недели'
              return `≈ ${Math.round(d / 7)} недели`
            }
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${c.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.07em', color: c.text, textTransform: 'uppercase' }}>
                    Counterfactual
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.20)' }}>·</span>
                  <span style={{ fontSize: 9.5, fontWeight: 600, color: c.text }}>
                    {insight.counterfactual_pressure_state.replace('_', ' ')}
                  </span>
                  {insight.counterfactual_transition_window_days && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.20)' }}>·</span>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.35)' }}>
                        ≈ {_wLabel(insight.counterfactual_transition_window_days)}
                      </span>
                    </>
                  )}
                </div>
                {insight.counterfactual_note && (
                  <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                    ↳ {insight.counterfactual_note}
                  </span>
                )}
                {insight.counterfactual_next_phase && (
                  <div style={{ marginTop: 5, fontSize: 10.5, color: 'rgba(255,255,255,0.22)', lineHeight: 1.4 }}>
                    Следующая вероятная фаза:{' '}
                    <span style={{ color: c.text }}>
                      {insight.counterfactual_next_phase}
                    </span>
                  </div>
                )}
                {insight.counterfactual_reversibility_remaining_pct != null && (
                  <div style={{ marginTop: 3, fontSize: 10.5, color: 'rgba(255,255,255,0.20)', lineHeight: 1.4 }}>
                    Операционная гибкость:{' '}
                    <span style={{ color: 'rgba(255,255,255,0.35)' }}>
                      ~{insight.counterfactual_reversibility_remaining_pct}%
                    </span>
                  </div>
                )}
              </div>
            )
          })()}

          {/* Stabilization lock — Sprint 38 */}
          {insight.recovery_signal_state && ['waiting', 'stabilizing'].includes(insight.recovery_signal_state) && (
            <div style={{
              marginBottom: 10, padding: '7px 10px', borderRadius: 5,
              background: 'rgba(255,255,255,0.01)',
              borderLeft: '2px solid rgba(110,106,252,0.14)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.07em', color: 'rgba(110,106,252,0.45)', textTransform: 'uppercase' }}>
                  Окно наблюдения
                </span>
                {insight.lock_estimated_recovery_window_days && (
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.22)' }}>
                    · ≈ {insight.lock_estimated_recovery_window_days} дней
                  </span>
                )}
              </div>
              <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                ↳ Сейчас система продолжает наблюдать эффект предыдущих изменений.
              </span>
              {insight.lock_reentry_condition && (
                <div style={{ marginTop: 4, fontSize: 10.5, color: 'rgba(255,255,255,0.22)', lineHeight: 1.4 }}>
                  {insight.lock_reentry_condition}
                </div>
              )}
              {insight.lock_next_safe_action && (
                <div style={{ marginTop: 3, fontSize: 10.5, color: 'rgba(110,106,252,0.45)', lineHeight: 1.4 }}>
                  {insight.lock_next_safe_action}
                </div>
              )}
            </div>
          )}

          {/* Signal lifecycle note — Sprint 24 */}
          {insight.signal_lifecycle_note && (() => {
            const LC_BORDER: Record<string, string> = {
              emerging:   'rgba(167,139,250,0.28)',
              confirmed:  'rgba(245,158,11,0.28)',
              stabilized: 'rgba(52,211,153,0.28)',
              recurring:  'rgba(248,113,113,0.28)',
              resolved:   'rgba(113,113,122,0.22)',
            }
            const border = LC_BORDER[insight.signal_lifecycle_stage ?? ''] ?? 'rgba(255,255,255,0.08)'
            return (
              <div style={{
                marginTop: 10, marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.015)',
                borderLeft: `2px solid ${border}`,
              }}>
                <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                  {insight.signal_lifecycle_note}
                </span>
              </div>
            )
          })()}

          {/* Signal decay note — Sprint 27 */}
          {insight.signal_decay_note && insight.signal_decay_state && insight.signal_decay_state !== 'fresh' && (() => {
            const DC_BORDER: Record<string, string> = {
              aging:      'rgba(167,139,250,0.20)',
              fading:     'rgba(245,158,11,0.20)',
              stale:      'rgba(113,113,122,0.20)',
              persistent: 'rgba(248,113,113,0.20)',
            }
            const border = DC_BORDER[insight.signal_decay_state] ?? 'rgba(255,255,255,0.08)'
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: `2px solid ${border}`,
              }}>
                <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                  ↳ {insight.signal_decay_note}
                </span>
              </div>
            )
          })()}

          {/* Decision stability note */}
          {insight.decision_stability_note && (
            <div style={{
              marginTop: 10, marginBottom: 12, padding: '7px 10px', borderRadius: 5,
              background: 'rgba(255,255,255,0.015)',
              borderLeft: '2px solid rgba(255,255,255,0.08)',
            }}>
              <span style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                {insight.decision_stability_note}
              </span>
            </div>
          )}

          {/* Demo notice */}
          {insight.is_demo && (
            <div style={{
              marginBottom: 12, padding: '7px 10px', borderRadius: 6,
              background: 'rgba(110,106,252,0.08)', border: `1px solid ${R.vDim}`,
              display: 'flex', alignItems: 'center', gap: 7,
            }}>
              <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: '#A78BFA' }}>DEMO</span>
              <span style={{ fontSize: 11.5, color: R.text3 }}>Пример на тестовых данных. Импортируйте CSV для реальных инсайтов.</span>
            </div>
          )}

          {/* Marketplace mechanic note */}
          {insight.marketplace_risk_note && insight.automation_level && (
            <div style={{
              marginBottom: 12, padding: '8px 10px', borderRadius: 6,
              background: AUTOMATION_META[insight.automation_level]?.bg ?? 'rgba(255,255,255,0.04)',
              border: `1px solid ${AUTOMATION_META[insight.automation_level]?.color ?? R.line}22`,
              display: 'flex', alignItems: 'flex-start', gap: 8,
            }}>
              <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: '0.07em', color: AUTOMATION_META[insight.automation_level]?.color, flexShrink: 0, paddingTop: 1 }}>
                {AUTOMATION_META[insight.automation_level]?.label.toUpperCase()}
              </span>
              <span style={{ fontSize: 11.5, color: R.text2, lineHeight: 1.5 }}>
                {insight.marketplace_risk_note}
              </span>
            </div>
          )}

          {/* Historical memory — subtle, never dominates */}
          {insight.memory_context && (
            <div style={{
              marginBottom: 10, padding: '6px 10px', borderRadius: 5,
              background: 'rgba(255,255,255,0.03)',
              borderLeft: `2px solid rgba(167,139,250,0.35)`,
              display: 'flex', alignItems: 'flex-start', gap: 6,
            }}>
              <span style={{ fontSize: 10, color: '#A78BFA', flexShrink: 0, marginTop: 1 }}>🧠</span>
              <span style={{ fontSize: 11.5, color: R.text3, fontStyle: 'italic', lineHeight: 1.5 }}>
                {insight.memory_context}
              </span>
            </div>
          )}

          {/* Reasons */}
          <div style={{ marginBottom: 10 }}>
            <p style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.09em', color: R.text3, marginBottom: 6, textTransform: 'uppercase' }}>
              Почему
            </p>
            <ul style={{ padding: 0, margin: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {insight.reasons.map((r, i) => (
                <li key={i} style={{ display: 'flex', gap: 7, fontSize: 13, color: R.text2, lineHeight: 1.45 }}>
                  <span style={{ color: ts.borderColor, flexShrink: 0, marginTop: 1 }}>·</span>{r}
                </li>
              ))}
            </ul>
          </div>

          {/* Comparative sandbox — Sprint 42 */}
          {insight.path_comparison && (() => {
            const pc = insight.path_comparison!
            const ACTION_LABELS: Record<string, string> = {
              reduce_ad_spend:       'Снижение рекламной нагрузки',
              campaign_restructure:  'Реструктуризация кампаний',
              increase_price:        'Корректировка цены',
              procurement_repricing: 'Repricing закупок',
              rebuild_seo:           'SEO-пересборка',
              optimize_keywords:     'Оптимизация ключевых слов',
              increase_ads:          'Рост рекламной нагрузки',
              stock_replenishment:   'Пополнение стока',
            }
            const SPEED_LABELS: Record<string, string> = {
              faster:   'stabilizes быстрее',
              moderate: 'умеренная скорость',
              slower:   'stabilizes медленнее',
            }
            const VOL_LABELS: Record<string, string> = {
              lower:    'volatility ниже',
              moderate: 'умеренная volatility',
              higher:   'volatility выше',
            }
            const OBS_LABELS: Record<string, string> = {
              preserved: 'observability сохранена',
              reduced:   'observability снижена',
              unclear:   'observability неясна',
            }
            const LOAD_LABELS: Record<string, string> = {
              lower:    'снижает operator load',
              moderate: 'умеренная нагрузка',
              higher:   'operator load выше',
            }
            const REV_LABELS: Record<string, string> = {
              stronger: 'reversibility: stronger',
              neutral:  'reversibility: neutral',
              weaker:   'reversibility: weaker',
            }
            const pathRows = (p: typeof pc.path_a) => [
              SPEED_LABELS[p.stabilization_speed] ?? p.stabilization_speed,
              VOL_LABELS[p.volatility_impact]      ?? p.volatility_impact,
              OBS_LABELS[p.observability_impact]   ?? p.observability_impact,
              LOAD_LABELS[p.operator_load]         ?? p.operator_load,
              REV_LABELS[p.reversibility_profile]  ?? p.reversibility_profile,
            ]
            return (
              <div style={{
                marginBottom: 12, padding: '10px 12px', borderRadius: 6,
                background: 'rgba(255,255,255,0.015)',
                border: '1px solid rgba(255,255,255,0.045)',
              }}>
                <p style={{
                  fontSize: 9, fontWeight: 900, letterSpacing: '0.13em',
                  color: R.text3, textTransform: 'uppercase', marginBottom: 10,
                }}>
                  Сравнение сценариев
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  {([pc.path_a, pc.path_b] as typeof pc.path_a[]).map((path, idx) => (
                    <div key={idx} style={{
                      padding: '8px 9px', borderRadius: 5,
                      background: 'rgba(255,255,255,0.02)',
                      borderLeft: `2px solid ${idx === 0 ? 'rgba(110,106,252,0.25)' : 'rgba(167,139,250,0.18)'}`,
                    }}>
                      <p style={{ fontSize: 11, fontWeight: 600, color: R.text2, marginBottom: 6, lineHeight: 1.3 }}>
                        {ACTION_LABELS[path.action_type] ?? path.action_type}
                      </p>
                      {pathRows(path).map((row, ri) => (
                        <p key={ri} style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.5, margin: 0 }}>
                          <span style={{ color: 'rgba(255,255,255,0.18)', marginRight: 4 }}>↳</span>
                          {row}
                        </p>
                      ))}
                    </div>
                  ))}
                </div>
                <p style={{
                  fontSize: 11, color: R.text3, fontStyle: 'italic',
                  lineHeight: 1.55, marginTop: 8, margin: '8px 0 0',
                }}>
                  {pc.contextual_note}
                </p>
              </div>
            )
          })()}

          {/* Observability recovery forecast — Sprint 44 */}
          {insight.obs_recovery_state && insight.obs_recovery_state !== 'clear' && (() => {
            const OBS_STATE_LABELS: Record<string, string> = {
              recovering:     'recovering',
              distorted:      'distorted',
              fragmented:     'fragmented',
              reset_required: 'reset required',
            }
            const OBS_STATE_COLORS: Record<string, { text: string; border: string; bg: string }> = {
              recovering:     { text: '#A78BFA', border: 'rgba(167,139,250,0.20)', bg: 'rgba(167,139,250,0.03)' },
              distorted:      { text: '#F59E0B', border: 'rgba(245,158,11,0.20)',  bg: 'rgba(245,158,11,0.025)' },
              fragmented:     { text: '#F59E0B', border: 'rgba(245,158,11,0.22)',  bg: 'rgba(245,158,11,0.03)'  },
              reset_required: { text: '#F87171', border: 'rgba(248,113,113,0.20)', bg: 'rgba(248,113,113,0.025)' },
            }
            const OBS_WINDOW_LABELS: Record<number, string> = {}
            const windowLabel = insight.obs_recovery_window_days
              ? insight.obs_recovery_window_days <= 7  ? '≈ 1 неделя'
              : insight.obs_recovery_window_days <= 14 ? '≈ 1–2 недели'
              : insight.obs_recovery_window_days <= 21 ? '≈ 2–3 недели'
              : '≈ 3–5 недель'
              : null
            const c = OBS_STATE_COLORS[insight.obs_recovery_state] ?? OBS_STATE_COLORS.distorted
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: c.bg, borderLeft: `2px solid ${c.border}`,
              }}>
                {/* Header row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.obs_recovery_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: c.text, textTransform: 'uppercase' }}>
                    Наблюдаемость
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: c.text }}>
                    {OBS_STATE_LABELS[insight.obs_recovery_state] ?? insight.obs_recovery_state}
                  </span>
                  {windowLabel && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: c.text, opacity: 0.75 }}>{windowLabel}</span>
                    </>
                  )}
                </div>
                {/* Narrative body */}
                {insight.obs_recovery_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: '0 0 6px' }}>
                    {insight.obs_recovery_note}
                  </p>
                )}
                {/* Footer: recovery condition + blocking factor */}
                {(insight.obs_recovery_condition || insight.obs_blocking_factor) && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.obs_recovery_condition && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.5 }}>
                        <span style={{ color: c.text, opacity: 0.55, marginRight: 4 }}>↳</span>
                        Восстановится {insight.obs_recovery_condition}.
                      </span>
                    )}
                    {insight.obs_blocking_factor && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.5 }}>
                        <span style={{ color: 'rgba(255,255,255,0.20)', marginRight: 4 }}>↳</span>
                        Сейчас мешает: {insight.obs_blocking_factor}.
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Intervention timing — Sprint 48 */}
          {insight.timing_state && insight.timing_state !== 'optimal' && (() => {
            const TIMING_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              immediate:           { label: 'immediate',           text: 'rgba(248,113,113,0.80)', border: 'rgba(248,113,113,0.28)', bg: 'rgba(248,113,113,0.03)'  },
              structurally_late:   { label: 'structurally late',   text: 'rgba(239,68,68,0.80)',   border: 'rgba(239,68,68,0.26)',   bg: 'rgba(239,68,68,0.03)'   },
              narrowing_window:    { label: 'narrowing window',    text: 'rgba(245,158,11,0.80)',  border: 'rgba(245,158,11,0.26)',  bg: 'rgba(245,158,11,0.025)' },
              emerging_window:     { label: 'emerging window',     text: 'rgba(52,211,153,0.70)',  border: 'rgba(52,211,153,0.22)',  bg: 'rgba(52,211,153,0.02)'  },
              stabilization_phase: { label: 'stabilization phase', text: 'rgba(96,165,250,0.65)',  border: 'rgba(96,165,250,0.20)',  bg: 'rgba(96,165,250,0.02)'  },
              observation_phase:   { label: 'observation phase',   text: 'rgba(167,139,250,0.70)', border: 'rgba(110,106,252,0.22)', bg: 'rgba(110,106,252,0.025)' },
            }
            const _wLabel = (d: number | null | undefined) => {
              if (!d) return null
              if (d <= 7)  return '≈ 1 неделя'
              if (d <= 14) return '≈ 1–2 недели'
              if (d <= 21) return '≈ 2–3 недели'
              if (d <= 30) return '≈ 3–4 недели'
              return '≈ 1 месяц'
            }
            const m      = TIMING_META[insight.timing_state] ?? TIMING_META.observation_phase
            const wLabel = _wLabel(insight.optimal_window_days)
            const hasFooter = !!(
              insight.readiness_condition || insight.waiting_benefit
              || insight.premature_risk_note || insight.delayed_risk_note
            )
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.timing_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Timing
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {wLabel && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.75 }}>{wLabel}</span>
                    </>
                  )}
                </div>
                {insight.timing_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.timing_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.readiness_condition && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: m.text, opacity: 0.55, marginRight: 4 }}>↳</span>
                        {insight.readiness_condition}
                      </span>
                    )}
                    {insight.waiting_benefit && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: 'rgba(255,255,255,0.18)', marginRight: 4 }}>↳</span>
                        {insight.waiting_benefit}
                      </span>
                    )}
                    {insight.premature_risk_note && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        {insight.premature_risk_note}
                      </span>
                    )}
                    {insight.delayed_risk_note && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        {insight.delayed_risk_note}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Opportunity cost — Sprint 45 */}
          {(() => {
            const cost = insight.future_intervention_cost
            const isContained = !cost || cost === 'minimal'
            const hasDep = !!insight.dependency_note
            const hasEscWindow = !!insight.forecast_instability_window_days
            if (isContained && !hasDep && !hasEscWindow) return null
            if (!insight.reversibility_shift_note) return null
            const OC_COLORS: Record<string, { border: string; accent: string }> = {
              minimal:    { border: 'rgba(255,255,255,0.06)',  accent: 'rgba(255,255,255,0.22)' },
              moderate:   { border: 'rgba(110,106,252,0.18)', accent: 'rgba(110,106,252,0.45)' },
              elevated:   { border: 'rgba(245,158,11,0.20)',  accent: 'rgba(245,158,11,0.55)'  },
              structural: { border: 'rgba(248,113,113,0.20)', accent: 'rgba(248,113,113,0.55)' },
            }
            const c = OC_COLORS[cost ?? 'minimal']
            return (
              <div style={{
                marginBottom: 10, padding: '7px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.005)',
                borderLeft: `2px solid ${c.border}`,
              }}>
                {insight.opportunity_cost_note && (
                  <p style={{ fontSize: 11, color: R.text3, fontStyle: 'italic', lineHeight: 1.5, margin: '0 0 4px' }}>
                    ↳ {insight.opportunity_cost_note}
                  </p>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {insight.dependency_note && (
                    <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.25)', lineHeight: 1.4 }}>
                      {insight.dependency_note}
                    </span>
                  )}
                  <span style={{ fontSize: 10.5, color: c.accent, lineHeight: 1.4, fontStyle: 'italic' }}>
                    {insight.reversibility_shift_note}
                  </span>
                </div>
              </div>
            )
          })()}

          {/* Intervention reversal — Sprint 49 */}
          {(() => {
            const rs = insight.reversal_state
            const prob = insight.reversal_probability ?? 0
            if (!rs || (rs === 'stable_intervention' && prob < 35)) return null
            const REVERSAL_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              stable_intervention: { label: 'stable',           text: 'rgba(255,255,255,0.30)', border: 'rgba(255,255,255,0.08)', bg: 'rgba(255,255,255,0.005)' },
              diminishing_return:  { label: 'diminishing return', text: 'rgba(245,158,11,0.80)',  border: 'rgba(245,158,11,0.26)',  bg: 'rgba(245,158,11,0.025)' },
              overextended:        { label: 'overextended',     text: 'rgba(251,146,60,0.85)',  border: 'rgba(249,115,22,0.28)',  bg: 'rgba(249,115,22,0.025)' },
              reversal_window:     { label: 'reversal window',  text: 'rgba(52,211,153,0.75)',  border: 'rgba(52,211,153,0.24)',  bg: 'rgba(52,211,153,0.02)'  },
              structurally_locked: { label: 'locked',           text: 'rgba(248,113,113,0.80)', border: 'rgba(248,113,113,0.26)', bg: 'rgba(248,113,113,0.03)' },
            }
            const _wLabel = (d: number | null | undefined) => {
              if (!d) return null
              if (d <= 7)  return '≈ 1 неделя'
              if (d <= 14) return '≈ 1–2 недели'
              if (d <= 21) return '≈ 2–3 недели'
              return '≈ 1 месяц'
            }
            const m      = REVERSAL_META[rs] ?? REVERSAL_META.diminishing_return
            const wLabel = _wLabel(insight.reversal_window_days)
            const hasFooter = !!(
              insight.reversal_trigger || insight.rollback_effect_expectation || insight.stabilization_dependency
            )
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.reversal_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Reversal
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {wLabel && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.75 }}>{wLabel}</span>
                    </>
                  )}
                </div>
                {insight.reversal_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.reversal_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.reversal_trigger && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: m.text, opacity: 0.50, marginRight: 4 }}>↳</span>
                        {insight.reversal_trigger}
                      </span>
                    )}
                    {insight.rollback_effect_expectation && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        {insight.rollback_effect_expectation}
                      </span>
                    )}
                    {insight.stabilization_dependency && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.22)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.14)', marginRight: 4 }}>↳</span>
                        {insight.stabilization_dependency}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Secondary pressure cascade — Sprint 50 */}
          {(() => {
            const cs = insight.cascade_state
            if (!cs || cs === 'isolated') return null
            const CASCADE_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              shifting_pressure:     { label: 'shifting pressure',   text: 'rgba(245,158,11,0.75)',  border: 'rgba(245,158,11,0.24)',  bg: 'rgba(245,158,11,0.02)'  },
              coupled_instability:   { label: 'coupled instability', text: 'rgba(251,146,60,0.85)',  border: 'rgba(249,115,22,0.28)',  bg: 'rgba(249,115,22,0.025)' },
              structurally_cascading: { label: 'systemic cascade',   text: 'rgba(248,113,113,0.85)', border: 'rgba(248,113,113,0.28)', bg: 'rgba(248,113,113,0.03)' },
            }
            const m = CASCADE_META[cs] ?? CASCADE_META.shifting_pressure
            const prob = insight.cascade_probability ?? 0
            const _wLabel = (d: number | null | undefined) => {
              if (!d) return null
              if (d <= 7)  return '≈ 1 неделя'
              if (d <= 14) return '≈ 1–2 недели'
              return '≈ 2–3 недели'
            }
            const wLabel = _wLabel(insight.cascade_window_days)
            const hasFooter = !!(insight.secondary_pressure_target || insight.cascade_offset_note)
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.cascade_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Cascade
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {prob > 0 && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.80 }}>{prob}%</span>
                    </>
                  )}
                  {wLabel && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.65 }}>{wLabel}</span>
                    </>
                  )}
                </div>
                {insight.cascade_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.cascade_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.secondary_pressure_target && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: m.text, opacity: 0.50, marginRight: 4 }}>↳</span>
                        Вероятно затронет: {insight.secondary_pressure_target}
                      </span>
                    )}
                    {insight.cascade_offset_note && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        {insight.cascade_offset_note}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Resilience snapshot — Sprint 51 */}
          {(() => {
            const rs = insight.resilience_state
            if (!rs || rs === 'adaptive') return null
            const RESILIENCE_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              resilient:  { label: 'resilient',  text: 'rgba(52,211,153,0.60)',  border: 'rgba(52,211,153,0.18)',  bg: 'rgba(52,211,153,0.015)' },
              moderate:   { label: 'moderate',   text: 'rgba(255,255,255,0.38)', border: 'rgba(255,255,255,0.10)', bg: 'rgba(255,255,255,0.005)' },
              narrowing:  { label: 'narrowing',  text: 'rgba(245,158,11,0.75)',  border: 'rgba(245,158,11,0.24)',  bg: 'rgba(245,158,11,0.02)'   },
              brittle:    { label: 'brittle',    text: 'rgba(251,146,60,0.85)',  border: 'rgba(249,115,22,0.28)',  bg: 'rgba(249,115,22,0.025)'  },
              collapsing: { label: 'collapsing', text: 'rgba(248,113,113,0.85)', border: 'rgba(248,113,113,0.28)', bg: 'rgba(248,113,113,0.03)'  },
              exhausted:  { label: 'exhausted',  text: 'rgba(239,68,68,0.85)',   border: 'rgba(239,68,68,0.28)',   bg: 'rgba(239,68,68,0.03)'    },
            }
            const m = RESILIENCE_META[rs] ?? RESILIENCE_META.moderate
            const _wLabel = (d: number | null | undefined) => {
              if (!d) return null
              if (d <= 7)  return '≈ 1 неделя'
              if (d <= 14) return '≈ 1–2 недели'
              if (d <= 21) return '≈ 2–3 недели'
              return '≈ 1 месяц'
            }
            const wLabel = _wLabel(insight.resilience_window)
            const hasFooter = !!(insight.weakest_operational_layer || insight.absorption_capacity)
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.resilience_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Resilience
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {wLabel && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.65 }}>{wLabel}</span>
                    </>
                  )}
                </div>
                {insight.resilience_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.resilience_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.weakest_operational_layer && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: m.text, opacity: 0.50, marginRight: 4 }}>↳</span>
                        Наиболее уязвимый слой: {insight.weakest_operational_layer}
                      </span>
                    )}
                    {insight.absorption_capacity && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4 }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        Absorption: {insight.absorption_capacity}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Resilience trajectory — Sprint 52 */}
          {(() => {
            const traj = insight.resilience_trajectory
            if (!traj || traj === 'stabilizing') return null
            const TRAJ_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              recovering:            { label: 'recovering',            text: 'rgba(52,211,153,0.70)',  border: 'rgba(52,211,153,0.22)',  bg: 'rgba(52,211,153,0.02)'  },
              degrading:             { label: 'degrading',             text: 'rgba(245,158,11,0.80)',  border: 'rgba(245,158,11,0.26)',  bg: 'rgba(245,158,11,0.025)' },
              structurally_degrading: { label: 'structurally degrading', text: 'rgba(248,113,113,0.85)', border: 'rgba(248,113,113,0.28)', bg: 'rgba(248,113,113,0.03)' },
            }
            const m = TRAJ_META[traj] ?? TRAJ_META.degrading
            const vel = insight.resilience_trajectory_velocity
            const hasFooter = !!(insight.absorption_transition_note)
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.resilience_trajectory_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Trajectory
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {vel && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.70 }}>{vel}</span>
                    </>
                  )}
                </div>
                {insight.resilience_trajectory_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.resilience_trajectory_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.absorption_transition_note && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        {insight.absorption_transition_note}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Adaptive capacity — Sprint 53 */}
          {(() => {
            const ac = insight.adaptive_capacity_state
            if (!ac || ac === 'adaptive') return null
            const AC_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              strengthening: { label: 'strengthening', text: 'rgba(52,211,153,0.70)',  border: 'rgba(52,211,153,0.22)',  bg: 'rgba(52,211,153,0.02)'  },
              plateauing:    { label: 'plateauing',    text: 'rgba(255,255,255,0.32)', border: 'rgba(255,255,255,0.09)', bg: 'rgba(255,255,255,0.005)' },
              rigid:         { label: 'rigid',         text: 'rgba(245,158,11,0.78)',  border: 'rgba(245,158,11,0.24)',  bg: 'rgba(245,158,11,0.02)'  },
              deteriorating: { label: 'deteriorating', text: 'rgba(251,146,60,0.85)',  border: 'rgba(249,115,22,0.28)',  bg: 'rgba(249,115,22,0.025)' },
            }
            const m = AC_META[ac] ?? AC_META.rigid
            const cycles = insight.adaptation_cycles
            const hasFooter = !!(insight.stabilization_trend || insight.observability_trend || insight.recurrence_trend)
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.adaptation_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Adaptation
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {cycles != null && cycles > 1 && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.60 }}>{cycles} цикла</span>
                    </>
                  )}
                </div>
                {insight.adaptation_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.adaptation_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.stabilization_trend && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: m.text, opacity: 0.50, marginRight: 4 }}>↳</span>
                        Стабилизация: {insight.stabilization_trend}
                      </span>
                    )}
                    {insight.observability_trend && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4 }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        Наблюдаемость: {insight.observability_trend}
                      </span>
                    )}
                    {insight.recurrence_trend && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.22)', lineHeight: 1.4 }}>
                        <span style={{ color: 'rgba(255,255,255,0.14)', marginRight: 4 }}>↳</span>
                        Повторяемость: {insight.recurrence_trend}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Strategic memory drift — Sprint 54 */}
          {(() => {
            const ds = insight.strategic_drift_state
            if (!ds || ds === 'aligned') return null
            const DRIFT_META: Record<string, { label: string; text: string; border: string; bg: string }> = {
              drifting:                { label: 'drifting',                text: 'rgba(245,158,11,0.65)',  border: 'rgba(245,158,11,0.20)',  bg: 'rgba(245,158,11,0.015)' },
              fragmented:              { label: 'fragmented',              text: 'rgba(251,146,60,0.82)',  border: 'rgba(249,115,22,0.26)',  bg: 'rgba(249,115,22,0.02)'  },
              historically_disconnected: { label: 'disconnected',          text: 'rgba(251,146,60,0.85)',  border: 'rgba(249,115,22,0.28)',  bg: 'rgba(249,115,22,0.025)' },
              compounding_repetition:  { label: 'compounding repetition',  text: 'rgba(248,113,113,0.85)', border: 'rgba(248,113,113,0.28)', bg: 'rgba(248,113,113,0.03)' },
            }
            const m = DRIFT_META[ds] ?? DRIFT_META.drifting
            const cycles = insight.historical_cycles
            const hasFooter = !!(insight.doctrine_alignment_note || insight.repetition_pattern_note)
            return (
              <div style={{
                marginBottom: 12, padding: '9px 11px', borderRadius: 6,
                background: m.bg, borderLeft: `2px solid ${m.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: insight.drift_note ? 5 : 0 }}>
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.10em', color: m.text, textTransform: 'uppercase' }}>
                    Memory
                  </span>
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: m.text }}>{m.label}</span>
                  {cycles != null && cycles > 1 && (
                    <>
                      <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.18)' }}>·</span>
                      <span style={{ fontSize: 10, color: m.text, opacity: 0.55 }}>{cycles} цикла</span>
                    </>
                  )}
                </div>
                {insight.drift_note && (
                  <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.55, fontStyle: 'italic', margin: hasFooter ? '0 0 5px' : '0' }}>
                    {insight.drift_note}
                  </p>
                )}
                {hasFooter && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {insight.doctrine_alignment_note && (
                      <span style={{ fontSize: 10.5, color: R.text3, lineHeight: 1.4 }}>
                        <span style={{ color: m.text, opacity: 0.50, marginRight: 4 }}>↳</span>
                        {insight.doctrine_alignment_note}
                      </span>
                    )}
                    {insight.repetition_pattern_note && (
                      <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.28)', lineHeight: 1.4, fontStyle: 'italic' }}>
                        <span style={{ color: 'rgba(255,255,255,0.16)', marginRight: 4 }}>↳</span>
                        {insight.repetition_pattern_note}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* Capacity defer note — Sprint 37 */}
          {(() => {
            if (!['saturated', 'overloaded'].includes(capacityState)) return null
            const cat = insight.key.split(':')[0]
            if (!deferCategories.includes(cat)) return null
            if ((insight.sequence_stage ?? 0) === 1) return null
            if (insight.reversibility_state === 'structurally_locked') return null
            if (insight.signal_lifecycle_stage === 'recurring') return null
            return (
              <div style={{
                marginBottom: 10, padding: '6px 10px', borderRadius: 5,
                background: 'rgba(255,255,255,0.01)',
                borderLeft: '2px solid rgba(110,106,252,0.18)',
              }}>
                <span style={{ fontSize: 10.5, color: 'rgba(110,106,252,0.50)', fontStyle: 'italic', lineHeight: 1.5 }}>
                  ↳ Может быть временно отложено до стабилизации более критичных зон.
                </span>
              </div>
            )
          })()}

          {/* Recommendations */}
          <div style={{ marginBottom: 14 }}>
            <p style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.09em', color: R.text3, marginBottom: 6, textTransform: 'uppercase' }}>
              Рекомендуем
            </p>
            <ul style={{ padding: 0, margin: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {insight.recommendations.map((r, i) => (
                <li key={i} style={{ display: 'flex', gap: 7, fontSize: 13, color: R.text, lineHeight: 1.45 }}>
                  <span style={{ color: ts.borderColor, flexShrink: 0, marginTop: 1 }}>•</span>{r}
                </li>
              ))}
            </ul>
          </div>

          {/* Outcome feedback note — Sprint 26 */}
          {insight.outcome_feedback_note && insight.recommendation_confidence_delta !== 0 && (() => {
            const delta  = insight.recommendation_confidence_delta ?? 0
            const border = delta === +10
              ? 'rgba(52,211,153,0.30)'
              : delta === -12
                ? 'rgba(239,68,68,0.22)'
                : 'rgba(245,158,11,0.22)'
            const color  = delta === +10
              ? 'rgba(52,211,153,0.70)'
              : delta === -12
                ? 'rgba(239,68,68,0.65)'
                : 'rgba(245,158,11,0.65)'
            return (
              <div style={{
                marginBottom: 10, padding: '7px 11px', borderRadius: 5,
                background: 'rgba(255,255,255,0.015)',
                borderLeft: `2px solid ${border}`,
              }}>
                <span style={{ fontSize: 11, color, lineHeight: 1.5, fontStyle: 'italic' }}>
                  ↳ {insight.outcome_feedback_note}
                </span>
              </div>
            )
          })()}

          {/* Adaptation note — quiet infrastructure, no labels */}
          {insight.adaptation_note && (
            <div style={{
              marginBottom: 10, padding: '6px 10px', borderRadius: 5,
              background: 'rgba(255,255,255,0.02)',
              borderLeft: '2px solid rgba(82,82,91,0.40)',
            }}>
              <span style={{ fontSize: 11, color: '#52525B', fontStyle: 'italic', lineHeight: 1.5 }}>
                ↳ {insight.adaptation_note}
              </span>
            </div>
          )}

          {/* Marketplace behavior memory — Sprint 20 */}
          {insight.marketplace_behavior_note && (
            <div style={{
              marginBottom: 10, padding: '8px 11px', borderRadius: 5,
              background: 'rgba(255,255,255,0.015)',
              borderLeft: '2px solid rgba(110,106,252,0.25)',
            }}>
              <p style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.08em', color: 'rgba(110,106,252,0.5)', marginBottom: 4, textTransform: 'uppercase' }}>
                ↳ Поведение площадки
              </p>
              <span style={{ fontSize: 11.5, color: R.text3, lineHeight: 1.55 }}>
                {insight.marketplace_behavior_note}
              </span>
              {insight.marketplace_stabilization_window && (
                <span style={{ display: 'block', marginTop: 4, fontSize: 10.5, color: 'rgba(110,106,252,0.45)' }}>
                  Окно стабилизации: ~{insight.marketplace_stabilization_window} дн.
                </span>
              )}
            </div>
          )}

          {/* Retrospective outcome memory — Sprint 21 */}
          {insight.outcome_memory_note && (
            <div style={{
              marginBottom: 10, padding: '8px 11px', borderRadius: 5,
              background: 'rgba(255,255,255,0.015)',
              borderLeft: '2px solid rgba(161,107,48,0.30)',
            }}>
              <p style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.08em', color: 'rgba(180,120,55,0.55)', marginBottom: 4, textTransform: 'uppercase' }}>
                ↳ История результата
              </p>
              <span style={{ fontSize: 11.5, color: R.text3, lineHeight: 1.55 }}>
                {insight.outcome_memory_note}
              </span>
            </div>
          )}

          {/* Metrics row */}
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 14 }}>
            <ConfidencePill level={insight.confidence_level} value={insight.confidence} />

            {insight.impact && (
              <span style={{
                fontWeight: insight.impact.sign === 'negative' ? 700 : 600,
                fontSize: insight.impact.sign === 'negative' ? 12 : 10.5,
                padding: insight.impact.sign === 'negative' ? '4px 10px' : '3px 8px',
                borderRadius: 20, whiteSpace: 'nowrap',
                background: insight.impact.sign === 'negative' ? R.redD : insight.impact.sign === 'positive' ? R.okD : 'rgba(255,255,255,0.05)',
                color: insight.impact.sign === 'negative' ? R.red : insight.impact.sign === 'positive' ? R.ok : R.text2,
              }}>
                {insight.impact.label}: {insight.impact.estimate}
              </span>
            )}

            {insight.benchmark && (
              <span style={{
                fontSize: 10.5, fontWeight: 600, padding: '3px 8px', borderRadius: 20, whiteSpace: 'nowrap',
                background: 'rgba(255,255,255,0.05)', color: R.text2,
              }}>
                {insight.benchmark.metric}: {insight.benchmark.value} · {insight.benchmark.deviation}
              </span>
            )}
          </div>

          {insight.benchmark && (
            <div style={{
              marginBottom: 14, padding: '8px 10px', borderRadius: 7,
              background: 'rgba(255,255,255,0.03)', border: `1px solid ${R.line}`,
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
            }}>
              <div>
                <p style={{ fontSize: 10, color: R.text3, marginBottom: 3 }}>{insight.benchmark.metric}</p>
                <p style={{ fontSize: 15, fontWeight: 700, color: R.text, letterSpacing: '-0.02em' }}>
                  {insight.benchmark.value}
                </p>
              </div>
              <div style={{ textAlign: 'right' }}>
                <p style={{ fontSize: 10, color: R.text3, marginBottom: 3 }}>Норма</p>
                <p style={{ fontSize: 13, color: R.text2 }}>{insight.benchmark.baseline}</p>
              </div>
            </div>
          )}

          {/* Actions + lifecycle */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            {/* Primary actions */}
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
              {insight.actions.map((action, i) => (
                <button
                  key={i}
                  onClick={() => onAction(action)}
                  style={{
                    fontWeight: 700, borderRadius: 7, cursor: 'pointer', transition: 'opacity 0.15s',
                    fontSize: action.type === 'primary' ? 13.5 : 12,
                    padding: action.type === 'primary' ? '10px 22px' : '6px 12px',
                    border: action.type === 'primary' ? 'none' : `1px solid ${R.line}`,
                    background: action.type === 'primary' ? R.v : 'rgba(255,255,255,0.05)',
                    color: action.type === 'primary' ? '#fff' : R.text2,
                  }}
                  onMouseEnter={e => { e.currentTarget.style.opacity = '0.8' }}
                  onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
                >
                  {action.label}
                </button>
              ))}
            </div>

            {/* Lifecycle controls */}
            {!insight.is_demo && (
              <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                {insight.status === 'active' && (
                  <>
                    <button
                      onClick={() => onStatus(insight.key, 'monitoring')}
                      disabled={isBusy}
                      style={{
                        fontSize: 11, fontWeight: 600, padding: '4px 8px',
                        borderRadius: 6, cursor: 'pointer',
                        border: `1px solid ${R.vDim}`,
                        background: R.vDim, color: '#A78BFA',
                        opacity: isBusy ? 0.5 : 1,
                      }}
                    >
                      <Eye size={10} style={{ display: 'inline', marginRight: 4, verticalAlign: 'middle' }} />
                      Наблюдение
                    </button>
                    <button
                      onClick={() => onStatus(insight.key, 'resolved')}
                      disabled={isBusy}
                      style={{
                        fontSize: 11, fontWeight: 600, padding: '4px 8px',
                        borderRadius: 6, cursor: 'pointer',
                        border: `1px solid ${R.okD}`,
                        background: R.okD, color: R.ok,
                        opacity: isBusy ? 0.5 : 1,
                      }}
                    >
                      <CheckCircle size={10} style={{ display: 'inline', marginRight: 4, verticalAlign: 'middle' }} />
                      Решено
                    </button>
                    <button
                      onClick={() => onStatus(insight.key, 'dismissed')}
                      disabled={isBusy}
                      style={{
                        fontSize: 11, padding: '4px 7px', borderRadius: 6,
                        cursor: 'pointer', border: `1px solid ${R.line}`,
                        background: 'transparent', color: R.text3,
                        opacity: isBusy ? 0.5 : 1,
                      }}
                      title="Скрыть"
                    >
                      ✕
                    </button>
                  </>
                )}
                {insight.status === 'monitoring' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 11, color: '#A78BFA' }}>Наблюдение…</span>
                    <button
                      onClick={() => onStatus(insight.key, 'resolved')}
                      disabled={isBusy}
                      style={{
                        fontSize: 11, fontWeight: 600, padding: '4px 8px',
                        borderRadius: 6, cursor: 'pointer',
                        border: `1px solid ${R.okD}`, background: R.okD, color: R.ok,
                        opacity: isBusy ? 0.5 : 1,
                      }}
                    >
                      Решено
                    </button>
                  </div>
                )}
                {insight.status === 'resolved' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <CheckCircle size={12} color={R.ok} />
                    <span style={{ fontSize: 11, color: R.ok, fontWeight: 600 }}>Решено</span>
                    <button
                      onClick={() => onStatus(insight.key, 'active')}
                      style={{ fontSize: 10, color: R.text3, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                    >
                      Вернуть
                    </button>
                  </div>
                )}
              </div>
            )}

            {insight.is_demo && (
              <span style={{ fontSize: 11, color: R.text3 }}>Импортируйте данные</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function ActionEnginePage() {
  const router   = useRouter()
  const [data,     setData]     = useState<InsightsResponse | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(false)
  const [tab,      setTab]      = useState<Tab>('all')
  const [updating, setUpdating] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(false)
    try { setData(await api.actionEngine.getInsights()) }
    catch { setError(true); trackEvent('action_engine_error_seen', 'action_engine') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    trackEvent('action_engine_opened', 'action_engine')
    load()
  }, [load])

  async function handleStatus(insightKey: string, status: string) {
    if (insightKey.startsWith('demo')) return
    setUpdating(insightKey)
    if (status === 'resolved') trackEvent('insight_resolved', 'action_engine', insightKey)
    if (status === 'dismissed') trackEvent('insight_snoozed', 'action_engine', insightKey)
    try {
      await api.actionEngine.updateStatus(insightKey, status)
      setData(await api.actionEngine.getInsights())
    } catch {
      await load()
    }
    finally { setUpdating(null) }
  }

  function handleAction(action: InsightAction) {
    if (action.params) {
      const q = new URLSearchParams(action.params as Record<string, string>)
      router.push(`${action.url}?${q.toString()}`)
    } else {
      router.push(action.url)
    }
  }

  // Filter by tab
  const allInsights = data?.insights ?? []
  const visible = allInsights.filter(ins => {
    const done = ins.status === 'resolved' || ins.status === 'dismissed'
    if (tab === 'resolved')  return done
    if (tab === 'warnings')  return ins.type === 'warning'  && !done
    if (tab === 'positive')  return ins.type === 'positive' && !done
    return !done
  })

  // Top-priority compressed list (focused: med/high conf, deduped by category, max 3)
  const topPriority = (data?.focused_insights ?? [])
    .filter(i => i.type === 'warning')
    .slice(0, 3)

  const isDemo = data?.is_demo ?? false

  // Tab counts
  const activeAll      = allInsights.filter(i => !['resolved','dismissed'].includes(i.status))
  const activeWarnings = activeAll.filter(i => i.type === 'warning')
  const activePositive = activeAll.filter(i => i.type === 'positive')
  const resolved       = allInsights.filter(i => ['resolved','dismissed'].includes(i.status))

  return (
    <div style={{ padding: '28px 32px 56px', maxWidth: 900 }}>

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: R.text, letterSpacing: '-0.02em', lineHeight: 1 }}>
            Операционная разведка
          </h1>
          <button
            onClick={load}
            disabled={loading}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              fontSize: 12, fontWeight: 600, color: R.text3,
              background: 'none', border: `1px solid ${R.line}`,
              borderRadius: 7, padding: '5px 10px', cursor: 'pointer',
              opacity: loading ? 0.5 : 1,
            }}
          >
            <RefreshCw size={11} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
            Обновить
          </button>
        </div>
        <p style={{ fontSize: 13, color: R.text3 }}>
          Rule-based анализ: проблемы, причины, действия, эффект изменений
        </p>
      </div>

      {/* ── Demo banner ───────────────────────────────────────────────────── */}
      {isDemo && (
        <div style={{
          marginBottom: 20, padding: '12px 16px',
          borderRadius: 9, border: `1px solid ${R.vDim}`,
          background: 'rgba(110,106,252,0.06)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.12em', color: '#A78BFA',
              background: R.vDim, padding: '3px 7px', borderRadius: 4 }}>DEMO</span>
            <span style={{ fontSize: 13, color: R.text2 }}>
              Пример на тестовых данных. Импортируйте реальные данные для анализа.
            </span>
          </div>
          <button
            onClick={() => router.push('/dashboard/import')}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              fontSize: 12, fontWeight: 700, color: '#A78BFA',
              background: R.vDim, border: 'none',
              borderRadius: 7, padding: '7px 12px', cursor: 'pointer', whiteSpace: 'nowrap',
            }}
          >
            <Upload size={12} /> Импортировать CSV
          </button>
        </div>
      )}

      {/* ── Summary stats ─────────────────────────────────────────────────── */}
      {!loading && data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 24 }}>
          {[
            {
              label: 'Активных проблем',
              value: data.total_warnings,
              color: data.total_warnings > 0 ? R.warn : R.ok,
              icon: <AlertTriangle size={13} color={data.total_warnings > 0 ? R.warn : R.ok} />,
            },
            {
              label: 'Позитивных сигналов',
              value: data.total_positive,
              color: R.ok,
              icon: <TrendingUp size={13} color={R.ok} />,
            },
            {
              label: 'Примерные потери',
              value: data.estimated_monthly_loss > 0
                ? `${Math.round(data.estimated_monthly_loss / 100) * 100 >= 1000
                    ? `${(Math.round(data.estimated_monthly_loss / 1000))}k`
                    : Math.round(data.estimated_monthly_loss)} ₽/мес`
                : '—',
              color: data.estimated_monthly_loss > 0 ? R.red : R.text3,
              icon: null,
              note: 'Оценка',
            },
          ].map((s, i) => (
            <div key={i} style={{
              background: R.surf, border: `1px solid ${R.line}`,
              borderRadius: 8, padding: '12px 14px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                {s.icon}
                <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', color: R.text3, textTransform: 'uppercase' }}>
                  {s.label}
                </p>
              </div>
              <p style={{ fontSize: 22, fontWeight: 700, color: s.color, letterSpacing: '-0.04em', lineHeight: 1 }}>
                {s.value}
              </p>
              {'note' in s && s.note && (
                <p style={{ fontSize: 10, color: R.text3, marginTop: 3 }}>{s.note}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── "Сегодня важно" compressed list ───────────────────────────────── */}
      {!loading && topPriority.length > 0 && (
        <div style={{
          marginBottom: 24,
          borderRadius: 9, overflow: 'hidden',
          border: `1px solid ${R.line}`,
          background: '#0D0D0F',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '9px 14px', borderBottom: `1px solid ${R.line}`,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: R.warn, display: 'inline-block', flexShrink: 0 }} />
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.10em', color: R.text3, textTransform: 'uppercase' }}>
              {topPriority.some(i => i.intervention_tier === 'immediate')
                ? 'Операционное давление'
                : 'Приоритет сейчас'}
            </span>
            <span style={{
              marginLeft: 'auto', fontSize: 10.5, fontWeight: 700,
              padding: '1px 6px', borderRadius: 4, color: R.warn,
              background: R.warnD,
            }}>
              {topPriority.length}
            </span>
          </div>
          {topPriority.map((ins, i) => (
            <div
              key={ins.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px',
                borderBottom: i < topPriority.length - 1 ? `1px solid rgba(35,35,41,0.8)` : 'none',
                cursor: 'pointer',
              }}
              onClick={() => setTab('warnings')}
            >
              <span style={{ fontSize: 14, flexShrink: 0 }}>{ins.icon}</span>
              <span style={{ fontSize: 13, color: R.text2, flex: 1, lineHeight: 1.3, overflow: 'hidden',
                textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ins.title}</span>
              {ins.impact && ins.impact.sign === 'negative' && (
                <span style={{ fontSize: 13, fontWeight: 800, color: R.red, flexShrink: 0 }}>
                  {ins.impact.estimate}
                </span>
              )}
              <ChevronRight size={11} color={R.text3} style={{ flexShrink: 0 }} />
            </div>
          ))}
        </div>
      )}

      {/* ── Operational Scenarios ─────────────────────────────────────────── */}
      {!loading && (data?.operational_scenarios?.length ?? 0) > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: R.v, display: 'inline-block', flexShrink: 0 }} />
            <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.12em', color: R.text3, textTransform: 'uppercase' }}>
              Операционные сценарии
            </span>
          </div>
          {data!.operational_scenarios.slice(0, 3).map((sc: OperationalScenario) => {
            const pathLabel = sc.path_type === 'conservative' ? 'Консервативный' : sc.path_type === 'balanced' ? 'Сбалансированный' : 'Агрессивный'
            const pathColor = sc.path_type === 'conservative' ? '#4ADE80' : sc.path_type === 'balanced' ? '#A78BFA' : '#F59E0B'
            return (
              <div key={sc.scenario_id} style={{
                marginBottom: 8, padding: '11px 14px',
                borderRadius: 8, background: R.surf, border: `1px solid ${R.line}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
                  <span style={{
                    fontSize: 9, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase',
                    color: pathColor, padding: '2px 6px', borderRadius: 4,
                    background: `${pathColor}18`,
                  }}>{pathLabel}</span>
                  <span style={{ fontSize: 11, color: R.text3, marginLeft: 'auto' }}>{sc.confidence}%</span>
                </div>
                <p style={{ fontSize: 12.5, color: R.text2, margin: '0 0 5px', lineHeight: 1.4 }}>{sc.assumption}</p>
                <p style={{ fontSize: 12, color: R.ok, margin: '0 0 6px', lineHeight: 1.3 }}>↗ {sc.expected_effect}</p>
                {sc.causal_chain.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    {sc.causal_chain.map((step: string, i: number) => (
                      <p key={i} style={{ fontSize: 11, color: R.text3, margin: '2px 0', lineHeight: 1.35 }}>
                        {i === 0 ? '→' : '↳'} {step}
                      </p>
                    ))}
                  </div>
                )}
                <p style={{
                  fontSize: 11, color: R.text3, margin: '6px 0 0',
                  borderTop: `1px solid ${R.line}`, paddingTop: 6, lineHeight: 1.35,
                }}>
                  Компромисс: {sc.tradeoff}
                </p>
                {sc.confidence_level === 'low' && (
                  <p style={{ fontSize: 10.5, color: '#78716C', margin: '4px 0 0', fontStyle: 'italic', lineHeight: 1.3 }}>
                    ⚠ {sc.uncertainty_note}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* ── Контекст портфеля — Sprint 22 + Sprint 28 ───────────────────── */}
      {!loading && (() => {
        const systemic = (data?.portfolio_patterns ?? []).filter(p => p.stabilization_complexity === 'systemic').slice(0, 3)
        if (systemic.length === 0) return null
        const _mpShort = (mp: string | null) => ({ wildberries: 'WB', ozon: 'Ozon', yandex_market: 'ЯМ' }[mp ?? ''] ?? mp ?? '')
        const _bandLabel = (band: string) => ({
          low:      'низкая уверенность',
          moderate: 'умеренная уверенность',
          stable:   'устойчивая уверенность',
          high:     'высокая уверенность',
        }[band] ?? band)
        return (
          <div style={{ marginBottom: 20 }}>
            <p style={{ fontSize: 9, fontWeight: 800, letterSpacing: '0.12em', color: R.text3, textTransform: 'uppercase', marginBottom: 8 }}>
              Контекст портфеля
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {systemic.map(p => (
                <div key={p.id} style={{
                  padding: '8px 12px', borderRadius: 6,
                  background: 'rgba(255,255,255,0.015)',
                  borderLeft: '2px solid rgba(239,68,68,0.20)',
                }}>
                  {p.marketplace && (
                    <span style={{ fontSize: 9.5, fontWeight: 700, color: 'rgba(239,68,68,0.45)', marginRight: 6 }}>
                      {_mpShort(p.marketplace)}:
                    </span>
                  )}
                  <span style={{ fontSize: 12, color: R.text3, lineHeight: 1.45 }}>
                    {p.operational_summary}
                  </span>
                  {(p.root_cause_hypothesis || p.cross_mp_memory_note) && (
                    <div style={{ marginTop: 5, display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {p.root_cause_hypothesis && (
                        <div style={{ fontSize: 10.5, color: 'rgba(110,106,252,0.65)', lineHeight: 1.35 }}>
                          {p.root_cause_hypothesis}
                          {p.root_cause_band && (
                            <span style={{ marginLeft: 5, color: R.text3, fontSize: 9.5 }}>
                              · {_bandLabel(p.root_cause_band)}
                            </span>
                          )}
                        </div>
                      )}
                      {p.cross_mp_memory_note && (
                        <div style={{ fontSize: 10.5, color: 'rgba(180,83,9,0.65)', lineHeight: 1.35 }}>
                          {p.cross_mp_memory_note}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )
      })()}

      {/* ── Portfolio direction line — Sprint 25 ─────────────────────────── */}
      {!loading && data?.operational_summary && (() => {
        const s: OperationalSummary = data!.operational_summary!
        const DIR_COLORS: Record<string, { text: string }> = {
          stabilizing:        { text: '#34D399' },
          unstable:           { text: '#F87171' },
          mixed:              { text: '#F59E0B' },
          expanding_pressure: { text: '#EF4444' },
        }
        const DIR_LABELS: Record<string, string> = {
          stabilizing: 'stabilizing', unstable: 'unstable',
          mixed: 'mixed', expanding_pressure: 'expanding pressure',
        }
        const c = DIR_COLORS[s.portfolio_direction] ?? DIR_COLORS.mixed
        return (
          <div style={{
            marginBottom: 14, display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: R.text3, textTransform: 'uppercase' }}>
              Portfolio direction
            </span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.20)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 600, color: c.text }}>
              {DIR_LABELS[s.portfolio_direction] ?? s.portfolio_direction}
            </span>
          </div>
        )
      })()}

      {/* ── Operator strategy rhythm — Sprint 40 ─────────────────────────── */}
      {!loading && data?.operator_strategy_profile && (() => {
        const sp = data!.operator_strategy_profile!
        const BAND_LABELS: Record<string, string> = {
          disciplined:      'disciplined operating rhythm',
          generally_stable: 'generally stable operating rhythm',
          elevated:         'elevated operational volatility',
          unstable:         'unstable operational pattern',
        }
        const BAND_COLORS: Record<string, string> = {
          disciplined:      '#34D399',
          generally_stable: R.text2,
          elevated:         '#F59E0B',
          unstable:         '#F87171',
        }
        const bandLabel = BAND_LABELS[sp.stability_band] ?? sp.stability_band
        const bandColor = BAND_COLORS[sp.stability_band] ?? R.text2
        return (
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: R.text3, textTransform: 'uppercase' }}>
                Стратегический ритм
              </span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.20)' }}>·</span>
              <span style={{ fontSize: 10, fontWeight: 600, color: bandColor }}>
                {bandLabel}
              </span>
            </div>
            {sp.coaching_note && (
              <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.5, margin: '4px 0 0', fontStyle: 'italic' }}>
                {sp.coaching_note}
              </p>
            )}
          </div>
        )
      })()}

      {/* ── Operational regime compact line — Sprint 55 ─────────────────── */}
      {!loading && data?.operational_regime && (() => {
        const reg: OperationalRegime = data!.operational_regime!
        if (reg.regime === 'expansion' && reg.regime_confidence < 60) return null
        const REGIME_ACCENT: Record<string, string> = {
          expansion:           '#4ADE80',
          stabilization:       '#A78BFA',
          defensive:           '#FCD34D',
          constrained:         '#F59E0B',
          containment:         '#F87171',
          recovery_transition: '#818CF8',
        }
        const REGIME_LABELS: Record<string, string> = {
          expansion:           'expansion',
          stabilization:       'stabilization',
          defensive:           'defensive',
          constrained:         'constrained',
          containment:         'containment',
          recovery_transition: 'recovery transition',
        }
        return (
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: R.text3, textTransform: 'uppercase' }}>
                Operational regime
              </span>
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.20)' }}>·</span>
              <span style={{ fontSize: 10, fontWeight: 600, color: REGIME_ACCENT[reg.regime] ?? R.text2 }}>
                {REGIME_LABELS[reg.regime] ?? reg.regime}
              </span>
            </div>
            {reg.regime !== 'expansion' && reg.regime !== 'stabilization' && (
              <p style={{ fontSize: 11, color: R.text3, lineHeight: 1.5, margin: '4px 0 0', fontStyle: 'italic' }}>
                {reg.regime_note}
              </p>
            )}
          </div>
        )
      })()}

      {/* ── Strategy commitment — Sprint 43 ───────────────────────────────── */}
      {!loading && data?.strategy_commitment && (() => {
        const sc: StrategyCommitment = data!.strategy_commitment!

        const TYPE_LABELS: Record<string, string> = {
          structural_margin_recovery: 'Structural margin recovery',
          advertising_stabilization:  'Advertising stabilization',
          seo_recovery:               'SEO recovery',
          volatility_reduction:       'Volatility reduction',
          growth_scaling:             'Growth scaling',
          inventory_stabilization:    'Inventory stabilization',
          mixed_fragmented_strategy:  'Mixed / fragmented',
        }
        const STATE_LABELS: Record<string, string> = {
          emerging:    'emerging',
          active:      'active',
          stabilizing: 'stabilizing',
          fragmented:  'fragmented',
          abandoned:   'abandoned',
        }
        const STATE_COLORS: Record<string, string> = {
          emerging:    '#A78BFA',
          active:      '#34D399',
          stabilizing: '#34D399',
          fragmented:  '#F59E0B',
          abandoned:   '#F87171',
        }
        const RISK_COLORS: Record<string, string> = {
          low:      '#34D399',
          moderate: '#F59E0B',
          high:     '#F87171',
        }
        const OBS_COLORS: Record<string, string> = {
          clear:     '#34D399',
          sufficient: R.text2,
          degraded:  '#F59E0B',
          unclear:   '#F87171',
        }

        const stateColor = STATE_COLORS[sc.commitment_state] ?? R.text2
        const isFragmented = sc.commitment_state === 'fragmented'
        const isAbandoned  = sc.commitment_state === 'abandoned'
        const showWarning  = isFragmented || isAbandoned

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Стабилизационная стратегия
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: 'rgba(255,255,255,0.015)',
              border: `1px solid ${showWarning ? 'rgba(245,158,11,0.15)' : R.line}`,
            }}>
              {/* Path + state row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: R.text }}>
                  {TYPE_LABELS[sc.strategy_type] ?? sc.strategy_type}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 700, color: stateColor, letterSpacing: '0.04em' }}>
                  {STATE_LABELS[sc.commitment_state] ?? sc.commitment_state}
                </span>
              </div>

              {/* Metrics row */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: sc.commitment_note ? 8 : 0 }}>
                {sc.estimated_observation_window_days && (
                  <span style={{ fontSize: 10.5, color: R.text3 }}>
                    observation window: <span style={{ color: R.text2 }}>≈ {sc.estimated_observation_window_days} дн.</span>
                  </span>
                )}
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  observability: <span style={{ color: OBS_COLORS[sc.observability_quality] ?? R.text2 }}>
                    {sc.observability_quality === 'clear' ? 'достаточно прозрачная' :
                     sc.observability_quality === 'sufficient' ? 'достаточно прозрачная' :
                     sc.observability_quality === 'degraded' ? 'снижена' : 'неясна'}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  interruption risk: <span style={{ color: RISK_COLORS[sc.interruption_risk] ?? R.text2 }}>
                    {sc.interruption_risk}
                  </span>
                </span>
              </div>

              {/* Narrative note */}
              {sc.commitment_note && (
                <p style={{
                  fontSize: 11, color: R.text3, lineHeight: 1.55,
                  fontStyle: 'italic', margin: 0,
                }}>
                  ↳ {sc.commitment_note}
                </p>
              )}

              {/* Fragmentation warning — no blame language */}
              {showWarning && (
                <p style={{
                  fontSize: 11, color: 'rgba(245,158,11,0.65)', lineHeight: 1.55,
                  fontStyle: 'italic', margin: '6px 0 0',
                }}>
                  {isFragmented
                    ? '↳ Некоторые stabilization paths менялись до завершения observation window.'
                    : '↳ Текущий stabilization path не продолжен до завершения observation cycle.'}
                </p>
              )}
            </div>
          </div>
        )
      })()}

      {/* ── Operational Regime — Sprint 55 ───────────────────────────────── */}
      {!loading && data?.operational_regime && (() => {
        const reg: OperationalRegime = data!.operational_regime!

        // Hide expansion when confidence < 60
        if (reg.regime === 'expansion' && reg.regime_confidence < 60) return null

        const REGIME_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          expansion:           { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.12)' },
          stabilization:       { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.12)' },
          defensive:           { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          constrained:         { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.18)' },
          containment:         { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.14)' },
          recovery_transition: { accent: '#818CF8', bg: 'rgba(129,140,248,0.04)', border: 'rgba(129,140,248,0.12)' },
        }
        const REGIME_LABELS: Record<string, string> = {
          expansion:           'Expansion',
          stabilization:       'Stabilization',
          defensive:           'Defensive',
          constrained:         'Constrained',
          containment:         'Containment',
          recovery_transition: 'Recovery transition',
        }
        const DIR_LABELS: Record<string, string> = {
          stabilizing:              'stabilizing',
          deteriorating:            'deteriorating',
          recovering:               'recovering',
          constrained:              'constrained',
          structurally_accumulating: 'structurally accumulating',
        }
        const TOL_LABELS: Record<string, string> = {
          high:     'high',
          moderate: 'moderate',
          selective: 'selective',
          narrow:   'narrow',
          minimal:  'minimal',
        }
        const OBS_LABELS: Record<string, string> = {
          strong:    'strong',
          moderate:  'moderate',
          degraded:  'degraded',
          fragmented: 'fragmented',
        }
        const TOL_COLORS: Record<string, string> = {
          high:     '#4ADE80',
          moderate: R.text2,
          selective: '#FCD34D',
          narrow:   '#F59E0B',
          minimal:  '#F87171',
        }
        const OBS_COLORS: Record<string, string> = {
          strong:    '#4ADE80',
          moderate:  R.text2,
          degraded:  '#F59E0B',
          fragmented: '#F87171',
        }

        const c = REGIME_COLORS[reg.regime] ?? REGIME_COLORS.stabilization

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Операционный режим
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg,
              border: `1px solid ${c.border}`,
            }}>
              {/* Regime + direction row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {REGIME_LABELS[reg.regime] ?? reg.regime}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 600, color: R.text3 }}>
                  {DIR_LABELS[reg.regime_direction] ?? reg.regime_direction}
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {reg.regime_note}
              </p>

              {/* Footer metrics */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  tolerance: <span style={{ color: TOL_COLORS[reg.intervention_tolerance] ?? R.text2 }}>
                    {TOL_LABELS[reg.intervention_tolerance] ?? reg.intervention_tolerance}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  observability: <span style={{ color: OBS_COLORS[reg.observability_quality] ?? R.text2 }}>
                    {OBS_LABELS[reg.observability_quality] ?? reg.observability_quality}
                  </span>
                </span>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── Decision Energy — Sprint 56 ──────────────────────────────────── */}
      {!loading && data?.decision_energy && (() => {
        const en = data!.decision_energy!

        // Hide lightweight when confidence < 60
        if (en.energy_state === 'lightweight' && en.energy_confidence < 60) return null

        const ENERGY_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          lightweight:            { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.10)' },
          manageable:             { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.10)' },
          draining:               { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          disruptive:             { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.20)' },
          structurally_exhausting: { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.14)' },
        }
        const STATE_LABELS: Record<string, string> = {
          lightweight:            'Lightweight',
          manageable:             'Manageable',
          draining:               'Draining',
          disruptive:             'Disruptive',
          structurally_exhausting: 'Structurally exhausting',
        }
        const LOAD_COLORS: Record<string, string> = {
          minimal:                '#4ADE80',
          isolated:               '#4ADE80',
          absorbable:             '#4ADE80',
          contained:              '#4ADE80',
          moderate:               R.text2,
          localized:              R.text2,
          sustained:              R.text2,
          'multi-step':           R.text2,
          elevated:               '#FCD34D',
          degraded:               '#FCD34D',
          cumulative:             '#FCD34D',
          'cross-functional':     '#FCD34D',
          high:                   '#F59E0B',
          fragmented:             '#F59E0B',
          expanding:              '#F59E0B',
          systemic:               '#F59E0B',
          structurally_distorted: '#F87171',
          structurally_depleting: '#F87171',
          structurally_coupled:   '#F87171',
        }

        const c = ENERGY_COLORS[en.energy_state] ?? ENERGY_COLORS.manageable

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Ресурс вмешательства
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              {/* State row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {STATE_LABELS[en.energy_state] ?? en.energy_state}
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {en.energy_note}
              </p>

              {/* Load metrics */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  coordination: <span style={{ color: LOAD_COLORS[en.coordination_load] ?? R.text2 }}>
                    {en.coordination_load}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  observability: <span style={{ color: LOAD_COLORS[en.observability_load] ?? R.text2 }}>
                    {en.observability_load}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  burden: <span style={{ color: LOAD_COLORS[en.stabilization_burden] ?? R.text2 }}>
                    {en.stabilization_burden}
                  </span>
                </span>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── Operational Phase — Sprint 57 ────────────────────────────────── */}
      {!loading && data?.operational_phase_transition && (() => {
        const pt: OperationalPhaseTransition = data!.operational_phase_transition!

        // Hide adaptive_equilibrium when confidence < 60
        if (pt.phase === 'adaptive_equilibrium' && pt.phase_confidence < 60) return null

        const PHASE_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          adaptive_equilibrium:          { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.10)' },
          stabilization_cycle:           { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.10)' },
          defensive_convergence:         { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          structural_pressure_formation: { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.20)' },
          resilience_fragmentation:      { accent: '#FB923C', bg: 'rgba(251,146,60,0.05)',  border: 'rgba(251,146,60,0.18)' },
          constrained_operation:         { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.16)' },
          recovery_reentry:              { accent: '#818CF8', bg: 'rgba(129,140,248,0.04)', border: 'rgba(129,140,248,0.12)' },
        }
        const PHASE_LABELS: Record<string, string> = {
          adaptive_equilibrium:          'Adaptive equilibrium',
          stabilization_cycle:           'Stabilization cycle',
          defensive_convergence:         'Defensive convergence',
          structural_pressure_formation: 'Structural pressure',
          resilience_fragmentation:      'Resilience fragmentation',
          constrained_operation:         'Constrained operation',
          recovery_reentry:              'Recovery reentry',
        }
        const DIR_COLORS: Record<string, string> = {
          stabilizing:  '#4ADE80',
          recovering:   '#818CF8',
          restrictive:  '#FCD34D',
          deteriorating: '#F87171',
        }
        const STAB_COLORS: Record<string, string> = {
          stable:     '#4ADE80',
          moderate:   R.text2,
          unstable:   '#F59E0B',
          fragmented: '#F87171',
        }

        const c = PHASE_COLORS[pt.phase] ?? PHASE_COLORS.stabilization_cycle

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Операционная фаза
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              {/* Header: phase · velocity transition */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {PHASE_LABELS[pt.phase] ?? pt.phase}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 500, color: R.text3 }}>
                  {pt.transition_velocity} transition
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {pt.phase_note}
              </p>

              {/* Footer: direction · stability · driver */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  direction: <span style={{ color: DIR_COLORS[pt.transition_direction] ?? R.text2 }}>
                    {pt.transition_direction}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  stability: <span style={{ color: STAB_COLORS[pt.transition_stability] ?? R.text2 }}>
                    {pt.transition_stability}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  driver: <span style={{ color: R.text2 }}>
                    {pt.transition_driver}
                  </span>
                </span>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── Stability Topology — Sprint 58 ───────────────────────────────── */}
      {!loading && data?.stability_topology && (() => {
        const topo: StabilityTopology = data!.stability_topology!

        if (topo.topology_state === 'balanced_stability' && topo.topology_confidence < 60) return null

        const TOPO_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          balanced_stability:      { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.10)' },
          compensating_structure:  { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.10)' },
          narrowing_support:       { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          fragmented_stability:    { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.20)' },
          structurally_unbalanced: { accent: '#FB923C', bg: 'rgba(251,146,60,0.05)',  border: 'rgba(251,146,60,0.18)' },
          collapsing_compensation: { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.16)' },
        }
        const STATE_LABELS: Record<string, string> = {
          balanced_stability:      'Balanced stability',
          compensating_structure:  'Compensating structure',
          narrowing_support:       'Narrowing support',
          fragmented_stability:    'Fragmented stability',
          structurally_unbalanced: 'Structurally unbalanced',
          collapsing_compensation: 'Collapsing compensation',
        }
        const FLEX_COLORS: Record<string, string> = {
          high:     '#4ADE80',
          moderate: R.text2,
          narrowing: '#FCD34D',
          limited:  '#F59E0B',
          low:      '#FB923C',
          minimal:  '#F87171',
        }
        const BAL_COLORS: Record<string, string> = {
          balanced: '#4ADE80',
          moderate: R.text2,
          fragile:  '#FCD34D',
          unstable: '#F59E0B',
          unbalanced: '#FB923C',
          collapsed:  '#F87171',
        }

        const c = TOPO_COLORS[topo.topology_state] ?? TOPO_COLORS.compensating_structure

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Топология устойчивости
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              {/* Header: state · remaining flexibility */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {STATE_LABELS[topo.topology_state] ?? topo.topology_state}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 500, color: FLEX_COLORS[topo.remaining_flexibility] ?? R.text2 }}>
                  {topo.remaining_flexibility} flexibility
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {topo.topology_note}
              </p>

              {/* Footer: support layer · weakest layer · compensation */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  support: <span style={{ color: R.text2 }}>{topo.dominant_stability_layer.replace(/_/g, ' ')}</span>
                </span>
                {topo.weakest_stability_layer !== 'none' && (
                  <span style={{ fontSize: 10.5, color: R.text3 }}>
                    weakest: <span style={{ color: FLEX_COLORS[topo.remaining_flexibility] ?? R.text2 }}>
                      {topo.weakest_stability_layer.replace(/_/g, ' ')}
                    </span>
                  </span>
                )}
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  balance: <span style={{ color: BAL_COLORS[topo.structural_balance] ?? R.text2 }}>
                    {topo.structural_balance}
                  </span>
                </span>
              </div>
            </div>
          </div>
        )
      })()}

      {!loading && data?.operational_doctrine && (() => {
        const doc: OperationalDoctrine = data!.operational_doctrine!

        if (doc.doctrine_state === 'adaptive_execution' && doc.doctrine_confidence < 60) return null

        const DOC_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          adaptive_execution:             { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.10)' },
          recurring_operational_bias:     { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.10)' },
          defensive_patterning:           { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          stabilization_dependency:       { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.20)' },
          structurally_embedded_doctrine: { accent: '#FB923C', bg: 'rgba(251,146,60,0.05)',  border: 'rgba(251,146,60,0.18)' },
          rigid_operational_doctrine:     { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.16)' },
        }
        const STATE_LABELS: Record<string, string> = {
          adaptive_execution:             'Adaptive execution',
          recurring_operational_bias:     'Recurring operational bias',
          defensive_patterning:           'Defensive patterning',
          stabilization_dependency:       'Stabilization dependency',
          structurally_embedded_doctrine: 'Structurally embedded doctrine',
          rigid_operational_doctrine:     'Rigid operational doctrine',
        }
        const FLEX_COLORS: Record<string, string> = {
          high:     '#4ADE80',
          moderate: R.text2,
          narrowing: '#FCD34D',
          limited:  '#F59E0B',
          low:      '#FB923C',
          minimal:  '#F87171',
        }

        const c = DOC_COLORS[doc.doctrine_state] ?? DOC_COLORS.recurring_operational_bias

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Операционная доктрина
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              {/* Header: state · flexibility */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {STATE_LABELS[doc.doctrine_state] ?? doc.doctrine_state}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 500, color: FLEX_COLORS[doc.doctrine_flexibility] ?? R.text2 }}>
                  {doc.doctrine_flexibility} flexibility
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {doc.doctrine_note}
              </p>

              {/* Footer: pattern / adaptation / institutionalization */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  pattern: <span style={{ color: R.text2 }}>{doc.doctrine_pattern.replace(/_/g, ' ')}</span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  adaptation: <span style={{ color: R.text2 }}>{doc.adaptation_mode.replace(/_/g, ' ')}</span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  institutionalization: <span style={{ color: c.accent }}>{doc.institutionalization_level}</span>
                </span>
              </div>
            </div>
          </div>
        )
      })()}

      {!loading && data?.institutional_inertia && (() => {
        const inertia: InstitutionalInertia = data!.institutional_inertia!

        if (inertia.inertia_state === 'flexible_structure' && inertia.inertia_confidence < 60) return null

        const INERTIA_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          flexible_structure:          { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.10)' },
          adaptive_inertia:            { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.10)' },
          operational_hardening:       { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          structural_inertia:          { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.20)' },
          locked_operational_behavior: { accent: '#FB923C', bg: 'rgba(251,146,60,0.05)',  border: 'rgba(251,146,60,0.18)' },
          institutional_freeze:        { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.16)' },
        }
        const STATE_LABELS: Record<string, string> = {
          flexible_structure:          'Flexible structure',
          adaptive_inertia:            'Adaptive inertia',
          operational_hardening:       'Operational hardening',
          structural_inertia:          'Structural inertia',
          locked_operational_behavior: 'Locked operational behavior',
          institutional_freeze:        'Institutional freeze',
        }
        const ELAST_COLORS: Record<string, string> = {
          high:      '#4ADE80',
          moderate:  R.text2,
          narrowing: '#FCD34D',
          low:       '#F59E0B',
          minimal:   '#FB923C',
          collapsed: '#F87171',
        }
        const RES_COLORS: Record<string, string> = {
          low:          '#4ADE80',
          moderate:     R.text2,
          elevated:     '#FCD34D',
          high:         '#F59E0B',
          very_high:    '#FB923C',
          extreme:      '#F87171',
        }

        const c = INERTIA_COLORS[inertia.inertia_state] ?? INERTIA_COLORS.adaptive_inertia

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Инерция системы
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              {/* Header: state · elasticity */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {STATE_LABELS[inertia.inertia_state] ?? inertia.inertia_state}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 500, color: ELAST_COLORS[inertia.structural_elasticity] ?? R.text2 }}>
                  {inertia.structural_elasticity} elasticity
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {inertia.inertia_note}
              </p>

              {/* Footer: resistance / elasticity / mobility */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  resistance: <span style={{ color: RES_COLORS[inertia.adaptation_resistance] ?? R.text2 }}>
                    {inertia.adaptation_resistance.replace(/_/g, ' ')}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  elasticity: <span style={{ color: ELAST_COLORS[inertia.structural_elasticity] ?? R.text2 }}>
                    {inertia.structural_elasticity}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  mobility: <span style={{ color: R.text2 }}>{inertia.recovery_mobility}</span>
                </span>
                {inertia.inertia_driver !== 'none' && (
                  <span style={{ fontSize: 10.5, color: R.text3 }}>
                    driver: <span style={{ color: R.text2 }}>{inertia.inertia_driver.replace(/_/g, ' ')}</span>
                  </span>
                )}
              </div>
            </div>
          </div>
        )
      })()}

      {!loading && data?.structural_recovery_capacity && (() => {
        const src: StructuralRecoveryCapacity = data!.structural_recovery_capacity!

        if (src.recovery_state === 'structurally_recoverable' && src.recovery_capacity_confidence < 60) return null

        const SRC_COLORS: Record<string, { accent: string; bg: string; border: string }> = {
          structurally_recoverable:    { accent: '#4ADE80', bg: 'rgba(74,222,128,0.04)',   border: 'rgba(74,222,128,0.10)' },
          recoverable_with_adaptation: { accent: '#A78BFA', bg: 'rgba(167,139,250,0.04)', border: 'rgba(167,139,250,0.10)' },
          constrained_recovery:        { accent: '#FCD34D', bg: 'rgba(252,211,77,0.04)',  border: 'rgba(252,211,77,0.12)' },
          restructuring_dependent:     { accent: '#F59E0B', bg: 'rgba(245,158,11,0.05)',  border: 'rgba(245,158,11,0.20)' },
          continuity_without_recovery: { accent: '#FB923C', bg: 'rgba(251,146,60,0.05)',  border: 'rgba(251,146,60,0.18)' },
          structurally_exhausted:      { accent: '#F87171', bg: 'rgba(248,113,113,0.04)', border: 'rgba(248,113,113,0.16)' },
        }
        const STATE_LABELS: Record<string, string> = {
          structurally_recoverable:    'Structurally recoverable',
          recoverable_with_adaptation: 'Recoverable with adaptation',
          constrained_recovery:        'Constrained recovery',
          restructuring_dependent:     'Restructuring dependent',
          continuity_without_recovery: 'Continuity without recovery',
          structurally_exhausted:      'Structurally exhausted',
        }
        const ELAST_COLORS: Record<string, string> = {
          high:       '#4ADE80',
          moderate:   R.text2,
          narrowing:  '#FCD34D',
          low:        '#F59E0B',
          restricted: '#FB923C',
          minimal:    '#F87171',
        }
        const REC_COLORS: Record<string, string> = {
          high:      '#4ADE80',
          moderate:  R.text2,
          limited:   '#FCD34D',
          fragile:   '#F59E0B',
          minimal:   '#FB923C',
          collapsed: '#F87171',
        }

        const c = SRC_COLORS[src.recovery_state] ?? SRC_COLORS.constrained_recovery

        return (
          <div style={{ marginBottom: 18 }}>
            <p style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '0.10em',
              color: R.text3, textTransform: 'uppercase', marginBottom: 8,
            }}>
              Способность к восстановлению
            </p>
            <div style={{
              padding: '10px 12px', borderRadius: 7,
              background: c.bg, border: `1px solid ${c.border}`,
            }}>
              {/* Header: recovery_state · elasticity */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: c.accent }}>
                  {STATE_LABELS[src.recovery_state] ?? src.recovery_state}
                </span>
                <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>·</span>
                <span style={{ fontSize: 10, fontWeight: 500, color: ELAST_COLORS[src.recovery_elasticity] ?? R.text2 }}>
                  {src.recovery_elasticity} elasticity
                </span>
              </div>

              {/* Narrative */}
              <p style={{
                fontSize: 11, color: R.text3, lineHeight: 1.55,
                fontStyle: 'italic', margin: '0 0 8px',
              }}>
                ↳ {src.recovery_capacity_note}
              </p>

              {/* Footer metrics */}
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  recoverability: <span style={{ color: REC_COLORS[src.structural_recoverability] ?? R.text2 }}>
                    {src.structural_recoverability}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  elasticity: <span style={{ color: ELAST_COLORS[src.recovery_elasticity] ?? R.text2 }}>
                    {src.recovery_elasticity}
                  </span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  restructuring: <span style={{ color: R.text2 }}>{src.restructuring_requirement}</span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  dependence: <span style={{ color: R.text2 }}>{src.continuity_dependence}</span>
                </span>
                <span style={{ fontSize: 10.5, color: R.text3 }}>
                  horizon: <span style={{ color: R.text2 }}>{src.structural_recovery_horizon.replace(/_/g, ' ')}</span>
                </span>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── Tabs ──────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, borderBottom: `1px solid ${R.line}`, paddingBottom: 0 }}>
        {([
          { key: 'all',      label: 'Все',          count: activeAll.length      },
          { key: 'warnings', label: 'Предупреждения', count: activeWarnings.length },
          { key: 'positive', label: 'Рост',          count: activePositive.length },
          { key: 'resolved', label: 'Решённые',       count: resolved.length       },
        ] as { key: Tab; label: string; count: number }[]).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              fontSize: 13, fontWeight: tab === t.key ? 600 : 400,
              padding: '8px 12px', borderRadius: '7px 7px 0 0',
              background: 'none', border: 'none', cursor: 'pointer',
              color: tab === t.key ? R.text : R.text3,
              borderBottom: tab === t.key ? `2px solid ${R.v}` : '2px solid transparent',
              transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            {t.label}
            {t.count > 0 && (
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '1px 5px', borderRadius: 4,
                background: tab === t.key ? R.vDim : 'rgba(255,255,255,0.06)',
                color: tab === t.key ? '#A78BFA' : R.text3,
              }}>
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Error state ──────────────────────────────────────────────────── */}
      {error && !loading && (
        <ErrorState
          onRetry={() => { trackEvent('retry_clicked', 'action_engine'); load() }}
        />
      )}

      {/* ── Insight list ──────────────────────────────────────────────────── */}
      {!error && loading ? (
        <SkeletonList count={3} height={80} />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<CheckCircle size={28} color={R.ok} />}
          title={tab === 'resolved' ? 'Решённых инсайтов нет' : 'Проблем не обнаружено'}
          body={tab === 'resolved'
            ? 'Отметьте решённые проблемы — они появятся здесь'
            : 'Пульт не нашёл критических отклонений в этой категории'}
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {visible.map(ins => (
            <InsightCard
              key={ins.id}
              insight={ins}
              onStatus={handleStatus}
              onAction={handleAction}
              updating={updating}
              deferCategories={data?.operational_capacity?.defer_categories ?? []}
              capacityState={data?.operational_capacity?.capacity_state ?? 'stable'}
            />
          ))}
        </div>
      )}

      <style>{`
        @keyframes spin  { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes pulse { 0%, 100% { opacity: 0.6; } 50% { opacity: 0.3; } }
      `}</style>
    </div>
  )
}

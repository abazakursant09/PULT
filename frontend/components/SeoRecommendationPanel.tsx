'use client'

import { useEffect, useState } from 'react'
import { TrendingUp, Sparkles, AlertCircle, RefreshCw } from 'lucide-react'
import { api, type RebuildRecommendation } from '@/lib/api'

const CONF_LABEL: Record<string, string> = {
  low:    'Мало данных',
  medium: 'Средняя',
  high:   'Высокая',
}

const CONF_COLOR: Record<string, string> = {
  low:    '#8A8A8A',
  medium: '#A78BFA',
  high:   '#10B981',
}

interface Props {
  /** Called when user clicks "Применить" — allows parent to set preset */
  onApplyStyle?: (styleName: string) => void
  compact?: boolean
}

export function SeoRecommendationPanel({ onApplyStyle, compact }: Props) {
  const [data,    setData]    = useState<RebuildRecommendation | null>(null)
  const [loading, setLoading] = useState(true)
  const [open,    setOpen]    = useState(true)

  useEffect(() => {
    api.rebuildTracker.recommendation()
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={panelStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 12px', opacity: 0.5 }}>
          <RefreshCw size={11} style={{ color: '#6E6AFC' }} />
          <span style={{ fontSize: 11, color: '#555' }}>Анализ истории...</span>
        </div>
      </div>
    )
  }

  if (!data || !data.has_data) return null

  const conf      = data.confidence || 'low'
  const confColor = CONF_COLOR[conf] || '#8A8A8A'
  const confLabel = CONF_LABEL[conf] || conf

  return (
    <div style={panelStyle}>
      {/* Header */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '9px 12px', background: 'none', border: 'none', cursor: 'pointer',
          borderBottom: open ? '1px solid rgba(110,106,252,0.12)' : 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 22, height: 22, borderRadius: 6,
            background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.22)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Sparkles size={11} color="#6E6AFC" />
          </div>
          <span style={{ fontSize: 11, fontWeight: 700, color: '#A78BFA', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            На основе rebuilds
          </span>
          {data.is_demo && (
            <span style={{
              fontSize: 9, fontWeight: 700, color: '#6E6AFC',
              background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.2)',
              borderRadius: 4, padding: '1px 5px', letterSpacing: '0.06em',
            }}>DEMO</span>
          )}
        </div>
        <span style={{ fontSize: 10, color: '#444', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
      </button>

      {open && (
        <div style={{ padding: '10px 12px' }}>
          {/* Best style */}
          {data.best_style_name && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 9, color: '#555', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 3 }}>
                Лучший стиль
              </div>
              <div style={{ fontSize: 12, fontWeight: 700, color: '#FFFFFF', lineHeight: 1.3 }}>
                {data.best_style_name}
              </div>
            </div>
          )}

          {/* Metrics row */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 8 }}>
            {data.avg_ctr_delta !== null && data.avg_ctr_delta !== undefined && (
              <div>
                <div style={{ fontSize: 9, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase' }}>CTR</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                  <TrendingUp size={11} color="#10B981" />
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#10B981' }}>+{data.avg_ctr_delta}%</span>
                </div>
              </div>
            )}

            <div>
              <div style={{ fontSize: 9, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase' }}>Rebuilds</div>
              <span style={{ fontSize: 13, fontWeight: 700, color: '#FFFFFF' }}>{data.rebuild_count}</span>
            </div>

            <div>
              <div style={{ fontSize: 9, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase' }}>Точность</div>
              <span style={{ fontSize: 11, fontWeight: 600, color: confColor }}>{confLabel}</span>
            </div>
          </div>

          {/* Winners badge */}
          {data.winners_count > 0 && (
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.18)',
              borderRadius: 5, padding: '3px 7px', marginBottom: 8,
            }}>
              <span style={{ fontSize: 10, color: '#10B981', fontWeight: 600 }}>
                🏆 {data.winners_count} победитель{data.winners_count > 1 ? 'а' : ''} A/B
              </span>
            </div>
          )}

          {/* Message (e.g. "no measured data") */}
          {data.message && (
            <div style={{ display: 'flex', gap: 5, alignItems: 'flex-start', marginBottom: 6 }}>
              <AlertCircle size={10} style={{ color: '#555', flexShrink: 0, marginTop: 1 }} />
              <span style={{ fontSize: 10, color: '#555', lineHeight: 1.4 }}>{data.message}</span>
            </div>
          )}

          {/* Demo disclaimer */}
          {data.is_demo && (
            <p style={{ fontSize: 10, color: '#444', margin: '4px 0 0', lineHeight: 1.4 }}>
              Данные для демонстрации. Появятся реальные данные после {3 - data.total_rebuilds} rebuild'а.
            </p>
          )}

          {/* Apply button */}
          {data.best_style_name && onApplyStyle && !compact && (
            <button
              type="button"
              onClick={() => onApplyStyle(data.best_style_name!)}
              style={{
                marginTop: 8, width: '100%',
                background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.25)',
                borderRadius: 6, padding: '6px 10px', fontSize: 11, fontWeight: 600,
                color: '#A78BFA', cursor: 'pointer', transition: 'background 0.2s',
              }}
            >
              ✨ Применить лучший стиль
            </button>
          )}
        </div>
      )}
    </div>
  )
}

const panelStyle: React.CSSProperties = {
  background: '#0D0D11',
  border: '1px solid rgba(110,106,252,0.18)',
  borderRadius: 12,
  overflow: 'hidden',
}

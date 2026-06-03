'use client'

import { useEffect, useState, useCallback } from 'react'
import { TrendingUp, Award, BarChart2, AlertCircle, RefreshCw, ChevronDown, Trophy } from 'lucide-react'
import { api, type StyleLeaderboardItem, type StyleDetailResponse } from '@/lib/api'

// ── Constants ─────────────────────────────────────────────────────────────────

const MARKETPLACES = [
  { value: 'all',           label: 'Все маркетплейсы' },
  { value: 'wildberries',   label: 'Wildberries' },
  { value: 'ozon',          label: 'Ozon' },
  { value: 'yandex_market', label: 'Яндекс Маркет' },
]

const CATEGORIES = [
  { value: 'all',         label: 'Все категории' },
  { value: 'tools',       label: 'Инструменты' },
  { value: 'auto',        label: 'Авто' },
  { value: 'cosmetics',   label: 'Косметика' },
  { value: 'electronics', label: 'Электроника' },
  { value: 'home',        label: 'Дом' },
  { value: 'other',       label: 'Другое' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function WinBadge({ pct }: { pct: number }) {
  const color = pct >= 65 ? '#10B981' : pct >= 45 ? '#A78BFA' : '#71717A'
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, color,
      background: `${color}18`, border: `1px solid ${color}30`,
      borderRadius: 5, padding: '2px 7px',
    }}>
      {pct}% win
    </span>
  )
}

function UpliftBadge({ delta }: { delta: number }) {
  const color = delta >= 10 ? '#10B981' : delta >= 5 ? '#6E6AFC' : '#71717A'
  return (
    <span style={{ fontSize: 13, fontWeight: 700, color }}>
      {delta > 0 ? '+' : ''}{delta.toFixed(1)}%
    </span>
  )
}

function FilterSelect({
  value, onChange, options,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div style={{ position: 'relative' }}>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          appearance: 'none',
          background: '#111113',
          border: '1px solid #2A2A32',
          borderRadius: 8,
          padding: '7px 34px 7px 12px',
          fontSize: 12,
          fontWeight: 600,
          color: '#D4D4D8',
          cursor: 'pointer',
          outline: 'none',
          minWidth: 160,
        }}
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <ChevronDown
        size={12}
        style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', color: '#555', pointerEvents: 'none' }}
      />
    </div>
  )
}

// ── Leaderboard table ─────────────────────────────────────────────────────────

function LeaderboardTable({
  items, onSelect, selected,
}: {
  items: StyleLeaderboardItem[]
  onSelect: (name: string) => void
  selected: string | null
}) {
  if (items.length === 0) return null

  return (
    <div style={{ border: '1px solid #1E1E26', borderRadius: 12, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        display: 'grid', gridTemplateColumns: '28px 1fr 80px 90px 70px',
        padding: '9px 16px',
        background: '#0D0D11',
        borderBottom: '1px solid #1E1E26',
      }}>
        {['#', 'Стиль', 'Win Rate', 'CTR Uplift', 'Кол-во'].map(h => (
          <span key={h} style={{ fontSize: 9.5, fontWeight: 700, color: '#555', letterSpacing: '0.08em', textTransform: 'uppercase' }}>{h}</span>
        ))}
      </div>

      {/* Rows */}
      {items.map((item, i) => {
        const isSelected = selected === item.style_name
        return (
          <div
            key={item.style_name}
            onClick={() => onSelect(item.style_name)}
            style={{
              display: 'grid', gridTemplateColumns: '28px 1fr 80px 90px 70px',
              padding: '11px 16px',
              background: isSelected ? 'rgba(110,106,252,0.07)' : 'transparent',
              borderBottom: i < items.length - 1 ? '1px solid #151519' : 'none',
              cursor: 'pointer',
              transition: 'background 0.15s',
              borderLeft: isSelected ? '2px solid #6E6AFC' : '2px solid transparent',
            }}
            onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.02)' }}
            onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
          >
            <span style={{ fontSize: 11, color: '#555', fontWeight: 700, paddingTop: 1 }}>{i + 1}</span>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#E4E4E7', lineHeight: 1.3 }}>{item.style_name}</div>
              {item.best_categories.length > 0 && (
                <div style={{ fontSize: 10, color: '#52525B', marginTop: 2 }}>
                  {item.best_categories.slice(0, 2).join(' · ')}
                </div>
              )}
            </div>
            <div style={{ paddingTop: 2 }}><WinBadge pct={item.win_rate} /></div>
            <div style={{ paddingTop: 2 }}><UpliftBadge delta={item.avg_ctr_uplift} /></div>
            <span style={{ fontSize: 12, color: '#71717A', paddingTop: 2 }}>{item.sample_size}</span>
          </div>
        )
      })}
    </div>
  )
}

// ── Style detail panel ────────────────────────────────────────────────────────

function StyleDetailPanel({ styleName, marketplace, category }: {
  styleName: string; marketplace: string; category: string
}) {
  const [data, setData]       = useState<StyleDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.seoIntelligence.styleDetail(styleName, { marketplace, category })
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [styleName, marketplace, category])

  if (loading) {
    return (
      <div style={panelStyle}>
        <div style={{ padding: 16, display: 'flex', alignItems: 'center', gap: 8, opacity: 0.5 }}>
          <RefreshCw size={13} style={{ color: '#6E6AFC' }} />
          <span style={{ fontSize: 12, color: '#555' }}>Загрузка...</span>
        </div>
      </div>
    )
  }

  if (!data) return null

  return (
    <div style={panelStyle}>
      {/* Header */}
      <div style={{ padding: '14px 16px 12px', borderBottom: '1px solid #1E1E26' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 8,
            background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <Award size={13} color="#6E6AFC" />
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#FFFFFF' }}>{data.style_name}</div>
            <div style={{ fontSize: 10, color: '#52525B' }}>
              {data.sample_size} измерений · {data.total_rebuilds} rebuilds
            </div>
          </div>
        </div>

        {/* Key metrics */}
        {data.has_data && (
          <div style={{ display: 'flex', gap: 16, marginTop: 10 }}>
            <div>
              <div style={{ fontSize: 9, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase' }}>CTR</div>
              <UpliftBadge delta={data.avg_ctr_uplift} />
            </div>
            <div>
              <div style={{ fontSize: 9, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase' }}>Win Rate</div>
              <WinBadge pct={data.win_rate} />
            </div>
          </div>
        )}
      </div>

      {/* Explanation */}
      <div style={{ padding: '12px 16px' }}>
        <div style={{ fontSize: 9.5, color: '#555', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 8 }}>
          🤖 Почему рекомендуем
        </div>
        {data.explanation_lines.length > 0 ? (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.explanation_lines.map((line, i) => (
              <li key={i} style={{
                fontSize: 12, color: '#A1A1AA', lineHeight: 1.5,
                display: 'flex', gap: 6, marginBottom: 4,
              }}>
                <span style={{ color: '#6E6AFC', flexShrink: 0 }}>•</span>
                {line}
              </li>
            ))}
          </ul>
        ) : (
          <p style={{ fontSize: 11, color: '#52525B', margin: 0 }}>
            Недостаточно данных. Используется базовая рекомендация.
          </p>
        )}

        {data.best_marketplaces.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 9.5, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 4 }}>Лучше на</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {data.best_marketplaces.map(mp => (
                <span key={mp} style={{
                  fontSize: 10, fontWeight: 600, color: '#A78BFA',
                  background: 'rgba(110,106,252,0.08)', border: '1px solid rgba(110,106,252,0.18)',
                  borderRadius: 5, padding: '2px 7px',
                }}>
                  {mp}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Footnote */}
        <p style={{ fontSize: 10, color: '#3A3A40', margin: '10px 0 0', lineHeight: 1.4 }}>
          Основано на {data.sample_size} rebuild'ах
        </p>
      </div>

      {/* Recent examples */}
      {data.recent_examples.length > 0 && (
        <div style={{ borderTop: '1px solid #151519', padding: '10px 16px' }}>
          <div style={{ fontSize: 9.5, color: '#555', fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', marginBottom: 8 }}>
            Последние примеры
          </div>
          {data.recent_examples.map((ex, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '5px 0',
              borderBottom: i < data.recent_examples.length - 1 ? '1px solid #151519' : 'none',
            }}>
              <div>
                <div style={{ fontSize: 11, color: '#D4D4D8', fontWeight: 500 }}>
                  {ex.product_name.slice(0, 28)}
                </div>
                <div style={{ fontSize: 10, color: '#52525B' }}>{ex.marketplace} · {ex.date}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: ex.delta_ctr >= 0 ? '#10B981' : '#EF4444' }}>
                  {ex.delta_ctr >= 0 ? '+' : ''}{ex.delta_ctr.toFixed(1)}%
                </span>
                {ex.winner && (
                  <Trophy size={10} color="#F59E0B" />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ isFiltered }: { isFiltered: boolean }) {
  return (
    <div style={{
      padding: '40px 24px', textAlign: 'center',
      border: '1px solid #1E1E26', borderRadius: 12,
      background: '#0D0D11',
    }}>
      <BarChart2 size={32} style={{ color: '#2A2A32', margin: '0 auto 12px' }} />
      <p style={{ fontSize: 14, fontWeight: 600, color: '#52525B', marginBottom: 6 }}>
        {isFiltered
          ? 'Недостаточно данных для выбранных фильтров'
          : 'Нет данных для анализа'}
      </p>
      <p style={{ fontSize: 12, color: '#3A3A40', maxWidth: 280, margin: '0 auto' }}>
        {isFiltered
          ? 'Попробуйте расширить фильтры или подождите накопления минимум 5 rebuilds в этой категории.'
          : 'Сгенерируйте минимум 5 SEO-карточек и добавьте CTR-метрики через PATCH /api/rebuild/{id}/metrics для получения аналитики.'}
      </p>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SeoIntelligencePage() {
  const [marketplace,  setMarketplace]  = useState('all')
  const [category,     setCategory]     = useState('all')
  const [leaderboard,  setLeaderboard]  = useState<StyleLeaderboardItem[]>([])
  const [hasData,      setHasData]      = useState(false)
  const [loading,      setLoading]      = useState(true)
  const [selectedStyle, setSelectedStyle] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.seoIntelligence.leaderboard({ marketplace, category })
      setLeaderboard(res.leaderboard)
      setHasData(res.has_data)
      if (res.leaderboard.length > 0 && !selectedStyle) {
        setSelectedStyle(res.leaderboard[0].style_name)
      }
    } catch {
      setLeaderboard([])
    } finally {
      setLoading(false)
    }
  }, [marketplace, category, selectedStyle])

  useEffect(() => { load() }, [marketplace, category])  // eslint-disable-line react-hooks/exhaustive-deps

  const isFiltered = marketplace !== 'all' || category !== 'all'

  return (
    <div style={{ padding: '28px 32px 48px', maxWidth: 1100 }}>

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <div style={{
            width: 34, height: 34, borderRadius: 10,
            background: 'rgba(110,106,252,0.12)', border: '1px solid rgba(110,106,252,0.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <TrendingUp size={16} color="#6E6AFC" />
          </div>
          <div>
            <h1 style={{ fontSize: 18, fontWeight: 700, color: '#FFFFFF', lineHeight: 1 }}>
              SEO Intelligence
            </h1>
            <p style={{ fontSize: 11, color: '#52525B', marginTop: 2 }}>
              Обучение системы на основе ваших rebuilds
            </p>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 24, flexWrap: 'wrap' }}>
        <FilterSelect value={marketplace} onChange={v => { setMarketplace(v); setSelectedStyle(null) }} options={MARKETPLACES} />
        <FilterSelect value={category}    onChange={v => { setCategory(v);    setSelectedStyle(null) }} options={CATEGORIES} />
        {isFiltered && (
          <button
            onClick={() => { setMarketplace('all'); setCategory('all'); setSelectedStyle(null) }}
            style={{
              background: 'none', border: '1px solid #2A2A32', borderRadius: 8,
              padding: '7px 12px', fontSize: 11, color: '#71717A', cursor: 'pointer',
            }}
          >
            Сбросить фильтры
          </button>
        )}
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 20 }}>
          <div style={{ border: '1px solid #1E1E26', borderRadius: 12, overflow: 'hidden' }}>
            {[0, 1, 2, 3].map(i => (
              <div key={i} className="skeleton" style={{ height: 52, margin: 0, borderRadius: 0, borderBottom: '1px solid #151519', animationDelay: `${i * 80}ms` }} />
            ))}
          </div>
          <div className="skeleton" style={{ borderRadius: 12, height: 280 }} />
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 20, alignItems: 'start' }}>
          {/* Left: leaderboard */}
          <div>
            {leaderboard.length === 0 ? (
              <EmptyState isFiltered={isFiltered} />
            ) : (
              <>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                  <p style={{ fontSize: 11, color: '#52525B' }}>
                    {leaderboard.length} стил{leaderboard.length === 1 ? 'ь' : 'я'} · мин. 5 rebuilds
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <AlertCircle size={10} style={{ color: '#52525B' }} />
                    <span style={{ fontSize: 10, color: '#3A3A40' }}>Кликните по строке для деталей</span>
                  </div>
                </div>
                <LeaderboardTable
                  items={leaderboard}
                  onSelect={setSelectedStyle}
                  selected={selectedStyle}
                />
              </>
            )}
          </div>

          {/* Right: detail panel */}
          <div>
            {selectedStyle ? (
              <StyleDetailPanel
                styleName={selectedStyle}
                marketplace={marketplace}
                category={category}
              />
            ) : (
              <div style={{ ...panelStyle, padding: '24px 16px', textAlign: 'center' }}>
                <Award size={24} style={{ color: '#2A2A32', margin: '0 auto 10px' }} />
                <p style={{ fontSize: 12, color: '#3A3A40' }}>
                  Выберите стиль слева для просмотра деталей
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Info footer */}
      {!loading && (
        <div style={{
          marginTop: 28, padding: '10px 16px', borderRadius: 8,
          background: 'rgba(110,106,252,0.04)', border: '1px solid rgba(110,106,252,0.1)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <AlertCircle size={12} style={{ color: '#6E6AFC', flexShrink: 0 }} />
          <span style={{ fontSize: 11, color: '#52525B', lineHeight: 1.5 }}>
            Аналитика обновляется при каждом новом rebuild. Добавляйте CTR-метрики через API
            чтобы система обучалась точнее. Минимум 5 измерений для статистической значимости.
          </span>
        </div>
      )}
    </div>
  )
}

const panelStyle: React.CSSProperties = {
  background: '#0D0D11',
  border: '1px solid #1E1E26',
  borderRadius: 12,
  overflow: 'hidden',
}

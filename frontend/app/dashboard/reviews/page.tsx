'use client'

import { useEffect, useState, useCallback } from 'react'
import { SellerBar } from '@/components/seller/Shell'
import { api, type ReviewResponse, type ReviewState, type ReviewHistoryEntry } from '@/lib/api'

// The seller's first review workspace (AR3). Uses the current design system — this is NOT the
// premium redesign. It shows only real synced reviews; a seller with none sees an honest empty
// state, never demo data.

const STATE_LABEL: Record<ReviewState, string> = {
  New: 'Новый', Processing: 'Обработка', Drafted: 'Черновик',
  NeedsAttention: 'Требует внимания', Approved: 'Одобрен', Published: 'Опубликован', Failed: 'Ошибка',
}
const STATE_COLOR: Record<ReviewState, string> = {
  New: '#8E8E93', Processing: '#8E8E93', Drafted: '#6E6AFC', NeedsAttention: '#F59E0B',
  Approved: '#22C55E', Published: '#16A34A', Failed: '#EF4444',
}
const FILTERS: (ReviewState | '')[] = ['', 'New', 'NeedsAttention', 'Drafted', 'Approved', 'Published', 'Failed']

function StateBadge({ s }: { s: ReviewState }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 6,
      color: STATE_COLOR[s], background: `${STATE_COLOR[s]}1A`, border: `1px solid ${STATE_COLOR[s]}40`,
    }}>{STATE_LABEL[s]}</span>
  )
}

function Stars({ n }: { n: number | null }) {
  if (n == null) return null
  return <span style={{ color: '#F59E0B', fontSize: 12 }}>{'★'.repeat(n)}{'☆'.repeat(Math.max(0, 5 - n))}</span>
}

export default function ReviewsPage() {
  const [items, setItems] = useState<ReviewResponse[] | null>(null)
  const [filter, setFilter] = useState<ReviewState | ''>('')
  const [error, setError] = useState('')
  const [sel, setSel] = useState<ReviewResponse | null>(null)
  const [history, setHistory] = useState<ReviewHistoryEntry[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [editText, setEditText] = useState('')

  const load = useCallback(() => {
    setItems(null); setError('')
    api.reviews.queue(filter ? { state: filter } : {})
      .then(r => setItems(r.items))
      .catch(e => { setError(e instanceof Error ? e.message : 'Ошибка'); setItems([]) })
  }, [filter])

  useEffect(() => { load() }, [load])

  function open(r: ReviewResponse) {
    setSel(r); setEditText(r.response_text ?? ''); setHistory(null)
    api.reviews.history(r.product_id, r.id).then(h => setHistory(h.entries)).catch(() => setHistory([]))
  }

  async function act(fn: () => Promise<ReviewResponse>) {
    if (!sel) return
    setBusy(true); setError('')
    try {
      const updated = await fn()
      setSel(updated); setEditText(updated.response_text ?? '')
      setItems(prev => prev ? prev.map(x => x.id === updated.id ? updated : x) : prev)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Действие не выполнено')
    } finally { setBusy(false) }
  }

  return (
    <>
      <SellerBar title="Отзывы" sub="Ответы на отзывы маркетплейсов" />
      <div className="s-canvas">
        <div className="flex gap-2 flex-wrap" style={{ marginBottom: 16 }}>
          {FILTERS.map(f => (
            <button key={f || 'all'} onClick={() => setFilter(f)}
              style={{
                fontSize: 12, padding: '5px 12px', borderRadius: 7, cursor: 'pointer',
                border: '1px solid var(--line)',
                background: filter === f ? 'var(--violet)' : 'var(--surface)',
                color: filter === f ? '#fff' : 'var(--text-2)',
              }}>{f ? STATE_LABEL[f] : 'Все'}</button>
          ))}
        </div>

        {error && <div className="s-card" style={{ color: 'var(--danger)', marginBottom: 12 }}>{error}</div>}

        {items === null ? (
          <div className="s-card" style={{ color: 'var(--text-3)' }}>Загрузка…</div>
        ) : items.length === 0 ? (
          <div className="s-card">
            <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>Отзывов пока нет</h2>
            <p style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 8 }}>
              Здесь появятся реальные отзывы после синхронизации с подключённым кабинетом
              маркетплейса. Демо-данные не показываются.
            </p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.1fr)', gap: 16 }}>
            {/* Queue */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {items.map(r => (
                <button key={r.id} onClick={() => open(r)} className="s-card" style={{
                  textAlign: 'left', cursor: 'pointer',
                  border: sel?.id === r.id ? '1px solid var(--violet)' : '1px solid var(--line)',
                }}>
                  <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
                    <StateBadge s={r.state} />
                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{r.marketplace ?? '—'}</span>
                  </div>
                  <Stars n={r.rating} />
                  <div style={{ fontSize: 13, color: 'var(--text)', marginTop: 4, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.review_text || <span style={{ color: 'var(--text-3)' }}>без текста</span>}
                  </div>
                </button>
              ))}
            </div>

            {/* Detail */}
            <div className="s-card" style={{ position: 'sticky', top: 16, alignSelf: 'start' }}>
              {!sel ? (
                <p style={{ fontSize: 13, color: 'var(--text-3)' }}>Выберите отзыв слева.</p>
              ) : (
                <>
                  <div className="flex items-center justify-between" style={{ marginBottom: 10 }}>
                    <StateBadge s={sel.state} />
                    <Stars n={sel.rating} />
                  </div>
                  <p style={{ fontSize: 13, color: 'var(--text)', marginBottom: 10 }}>{sel.review_text || '—'}</p>

                  {sel.safety_category && sel.safety_category !== 'SAFE' && (
                    <div style={{ fontSize: 12, color: '#F59E0B', background: '#F59E0B14',
                      border: '1px solid #F59E0B40', borderRadius: 7, padding: '8px 10px', marginBottom: 10 }}>
                      {sel.manual_required_reason || 'Требует ручной проверки'}
                    </div>
                  )}

                  {sel.state === 'Failed' && sel.failure_reason && (
                    <div style={{ fontSize: 12, color: 'var(--danger)', background: '#EF444414',
                      border: '1px solid #EF444440', borderRadius: 7, padding: '8px 10px', marginBottom: 10 }}>
                      Ошибка публикации: {sel.failure_reason} (попыток: {sel.publication_attempts})
                    </div>
                  )}

                  <textarea value={editText} onChange={e => setEditText(e.target.value)}
                    placeholder="Текст ответа" rows={4} className="input"
                    style={{ width: '100%', marginBottom: 10 }} disabled={sel.state === 'Published'} />

                  <div className="flex gap-2 flex-wrap">
                    <button disabled={busy} onClick={() => act(() => api.reviews.draft(sel.product_id, sel.id))}
                      className="s-btn gho">Создать черновик</button>
                    <button disabled={busy} onClick={() => act(() =>
                      api.reviews.update(sel.product_id, sel.id, { response_text: editText }))}
                      className="s-btn gho">Сохранить</button>
                    <button disabled={busy} onClick={() => act(() => api.reviews.approve(sel.product_id, sel.id))}
                      className="s-btn gho">Одобрить</button>
                    <button disabled={busy} onClick={() => act(() => api.reviews.publish(sel.product_id, sel.id))}
                      className="s-btn pri">Опубликовать</button>
                  </div>

                  <div style={{ marginTop: 16, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase' }}>
                      История публикаций
                    </div>
                    {history === null ? (
                      <p style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 6 }}>Загрузка…</p>
                    ) : history.length === 0 ? (
                      <p style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 6 }}>Публикаций ещё не было.</p>
                    ) : (
                      <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {history.map((h, i) => (
                          <div key={i} style={{ fontSize: 12, color: 'var(--text-2)' }}>
                            {h.timestamp?.slice(0, 19).replace('T', ' ')} · {h.mode} · {h.status}
                            {h.error_code ? ` · ${h.error_code}` : ''}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  )
}

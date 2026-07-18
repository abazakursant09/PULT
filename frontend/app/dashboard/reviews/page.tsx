'use client'

import { useEffect, useState, useCallback } from 'react'
import { SellerBar } from '@/components/seller/Shell'
import { api, type ReviewResponse, type ReviewState, type ReviewHistoryEntry } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { AutoReviewsPanel } from '@/components/reviews/AutoReviewsPanel'
import { STATE_LABEL, STATE_BADGE_VARIANT } from '@/lib/reviewState'
import { parseUtc, formatDateTime } from '@/lib/datetime'

// The seller's review workspace (AR3), P3 premium redesign. Presentation only — it shows the same
// real synced reviews from the same review APIs, with the same actions. No new functionality: a
// seller with no reviews sees an honest empty state, never demo data. All colour is on P0 tokens;
// status is communicated by a coloured P1 Badge, loading by a Skeleton, actions by P1 Buttons.

const FILTERS: (ReviewState | '')[] = ['', 'New', 'NeedsAttention', 'Drafted', 'Approved', 'Published', 'Failed']

const _MP_NAME: Record<string, string> = {
  wb: 'Wildberries', wildberries: 'Wildberries', ozon: 'Ozon',
  yandex: 'Яндекс Маркет', yandex_market: 'Яндекс Маркет', megamarket: 'Megamarket',
}
function mp(m: string | null): string {
  return m ? (_MP_NAME[m] || m) : '—'
}

function StateBadge({ s }: { s: ReviewState }) {
  return <Badge variant={STATE_BADGE_VARIANT[s]} className="text-[11px] rounded-[6px]">{STATE_LABEL[s]}</Badge>
}

function Stars({ n }: { n: number | null }) {
  if (n == null) return null
  return (
    <span className="text-[12px] leading-none [font-variant-numeric:tabular-nums]" style={{ color: 'var(--warning)' }}
      aria-label={`Оценка ${n} из 5`}>
      {'★'.repeat(n)}<span className="text-[var(--text-3)]">{'☆'.repeat(Math.max(0, 5 - n))}</span>
    </span>
  )
}

// ── AR-VIS-2: the fate of one reply, in the seller's language ───────────────────────────────────
// Everything below is derived from fields the backend already stores. Two rules hold throughout:
// nothing internal reaches the screen (no automated_l4 / manual_l3 / MARKETPLACE_4XX, no raw
// failure_reason — it can carry the marketplace's own response body), and nothing is invented. Where
// a cause is not stored, we stay silent instead of guessing; safe causes are AR-VIS-3.

function _fate(r: ReviewResponse): string {
  switch (r.state) {
    case 'Published': {
      const at = parseUtc(r.published_at)
      return at ? `Ответ опубликован ${formatDateTime(at)}` : 'Ответ опубликован'
    }
    case 'Approved':       return 'Ответ одобрен и ждёт публикации'
    case 'Drafted':        return 'Черновик готов — нужно ваше подтверждение'
    case 'NeedsAttention': return 'Этот отзыв отвечается вручную'
    case 'Failed':         return `Опубликовать не удалось. Попыток: ${r.publication_attempts}`
    default:               return 'Ответ ещё не готовился'
  }
}

// A publish attempt in plain Russian. `mode` is the ONLY place where "automatic vs by hand" is
// actually recorded (ExecutionLog.mode), so it is the only place we say it — a draft is never
// labelled automatic, because who created it is not stored anywhere.
function _attemptText(h: ReviewHistoryEntry): string {
  if (h.status === 'success') {
    return h.mode === 'automated_l4' ? 'опубликовано автоматически' : 'опубликовано вручную'
  }
  if (h.status === 'failed') return 'попытка не удалась'
  if (h.status === 'ambiguous') return 'результат не подтверждён, проверьте кабинет маркетплейса'
  if (h.status === 'rejected') return 'публикация отклонена проверками PULT'
  if (h.status === 'pending') return 'попытка выполняется'
  return 'попытка завершена'          // unknown status — neutral, never a guess
}

function AttemptRow({ h }: { h: ReviewHistoryEntry }) {
  const at = parseUtc(h.timestamp)
  return (
    <div className="text-[12px] text-[var(--text-2)] [font-variant-numeric:tabular-nums]">
      {at ? `${formatDateTime(at)} — ` : ''}{_attemptText(h)}
    </div>
  )
}

// Safety is a trust decision — show it plainly for every review, not only when it blocks. A SAFE
// review gets a calm, positive confirmation; anything else gets the honest reason it needs a human.
function SafetyLine({ r }: { r: ReviewResponse }) {
  const safe = r.safety_category === 'SAFE'
  if (safe) {
    return (
      <div className="text-[12px] rounded-[7px] px-2.5 py-2 mb-2.5 flex items-center gap-2 bg-[var(--success-dim)] text-[var(--success)]">
        <span aria-hidden>✓</span>
        <span>Безопасно для автоответа</span>
      </div>
    )
  }
  return (
    <div className="text-[12px] rounded-[7px] px-2.5 py-2 mb-2.5 bg-[var(--warning-dim)] text-[var(--warning)]">
      {r.manual_required_reason || 'Требует ручной проверки'}
    </div>
  )
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
        {/* AR-CONTROL-UI: per-connection Auto Reviews management. Default OFF; explicit consent
            before enabling; backend enforces every rule. */}
        <section className="mb-5">
          <p className="text-[11px] font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--text-3)' }}>
            Автоответы на отзывы
          </p>
          <AutoReviewsPanel />
        </section>

        {/* Filters — the accent one marks the active state */}
        <div className="flex gap-2 flex-wrap mb-4">
          {FILTERS.map(f => (
            <Button key={f || 'all'} onClick={() => setFilter(f)}
              variant={filter === f ? 'primary' : 'secondary'} size="sm">
              {f ? STATE_LABEL[f] : 'Все'}
            </Button>
          ))}
        </div>

        {error && (
          <Card variant="surface" className="mb-3 p-3.5 text-[13px] text-[var(--danger)]">{error}</Card>
        )}

        {items === null ? (
          // Loading — skeleton queue, no raw text, no layout shift
          <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.1fr)' }}>
            <div className="flex flex-col gap-2" aria-label="Загрузка">
              {[0, 1, 2].map(i => (
                <Card key={i} variant="surface" className="p-3.5 flex flex-col gap-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-3.5 w-16" />
                  <Skeleton className="h-3.5 w-full" />
                </Card>
              ))}
            </div>
            <Card variant="surface" className="p-4"><Skeleton className="h-40 w-full" /></Card>
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title="Отзывов пока нет"
            description="Здесь появятся реальные отзывы после синхронизации с подключённым кабинетом маркетплейса. Демо-данные не показываются."
          />
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.1fr)' }}>
            {/* Queue */}
            <div className="flex flex-col gap-2">
              {items.map(r => {
                const active = sel?.id === r.id
                return (
                  <button key={r.id} onClick={() => open(r)} className="text-left group">
                    <Card
                      variant={active ? 'elevated' : 'surface'}
                      className="p-3.5 transition-[border-color,background-color] duration-150 group-hover:bg-[var(--surface-h)]"
                      style={active ? { borderColor: 'var(--violet)' } : undefined}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <StateBadge s={r.state} />
                        <span className="text-[11px] text-[var(--text-3)]">{mp(r.marketplace)}</span>
                      </div>
                      <Stars n={r.rating} />
                      <div className="text-[13px] text-[var(--text)] mt-1 overflow-hidden text-ellipsis whitespace-nowrap">
                        {r.review_text || <span className="text-[var(--text-3)]">без текста</span>}
                      </div>
                    </Card>
                  </button>
                )
              })}
            </div>

            {/* Detail */}
            <Card variant="surface" className="p-4 sticky top-4 self-start">
              {!sel ? (
                <p className="text-[13px] text-[var(--text-3)]">Выберите отзыв слева.</p>
              ) : (
                <>
                  <div className="flex items-center justify-between mb-2.5">
                    <div className="flex items-center gap-2">
                      <StateBadge s={sel.state} />
                      <span className="text-[11px] text-[var(--text-3)]">{mp(sel.marketplace)}</span>
                    </div>
                    <Stars n={sel.rating} />
                  </div>
                  <p className="text-[13px] text-[var(--text)] mb-2.5">{sel.review_text || '—'}</p>

                  <SafetyLine r={sel} />

                  {/* What happened to this reply. The failed case says only that it failed and how
                      many times — the stored failure_reason is the marketplace's raw text and is
                      deliberately not shown; a safe, translated cause is AR-VIS-3. */}
                  <div
                    className={`text-[12px] rounded-[7px] px-2.5 py-2 mb-2.5 ${
                      sel.state === 'Failed'
                        ? 'bg-[var(--danger-dim)] text-[var(--danger)]'
                        : 'text-[var(--text-2)]'
                    }`}
                  >
                    {_fate(sel)}
                  </div>

                  <label className="block text-[11px] font-semibold uppercase tracking-wide text-[var(--text-3)] mb-1.5">
                    Ответ на отзыв
                  </label>
                  <textarea value={editText} onChange={e => setEditText(e.target.value)}
                    placeholder="Текст ответа" rows={4}
                    disabled={sel.state === 'Published'}
                    className="w-full mb-3 rounded-[var(--r-sm)] border border-[var(--line)] bg-[var(--bg)] text-[var(--text)] placeholder:text-[var(--text-3)] text-[14px] px-3 py-2.5 transition-[border-color] duration-[var(--dur)] focus-visible:outline-none focus-visible:border-[var(--violet-text)] disabled:opacity-40 disabled:cursor-not-allowed" />

                  {/* Actions grouped by lifecycle: prepare (draft/save) → approve → publish (primary) */}
                  <div className="flex gap-2 flex-wrap">
                    <Button variant="ghost" size="sm" disabled={busy}
                      onClick={() => act(() => api.reviews.draft(sel.product_id, sel.id))}>Создать черновик</Button>
                    <Button variant="ghost" size="sm" disabled={busy}
                      onClick={() => act(() => api.reviews.update(sel.product_id, sel.id, { response_text: editText }))}>Сохранить</Button>
                    <Button variant="secondary" size="sm" disabled={busy}
                      onClick={() => act(() => api.reviews.approve(sel.product_id, sel.id))}>Одобрить</Button>
                    <Button variant="primary" size="sm" disabled={busy}
                      onClick={() => act(() => api.reviews.publish(sel.product_id, sel.id))}>Опубликовать</Button>
                  </div>

                  <div className="mt-4 border-t border-[var(--line)] pt-3">
                    <div className="text-[11px] font-bold uppercase tracking-wide text-[var(--text-3)]">
                      История публикаций
                    </div>
                    {history === null ? (
                      <div className="mt-2 flex flex-col gap-1.5" aria-label="Загрузка">
                        <Skeleton className="h-3.5 w-3/4" />
                        <Skeleton className="h-3.5 w-1/2" />
                      </div>
                    ) : history.length === 0 ? (
                      <p className="text-[12px] text-[var(--text-3)] mt-1.5">Публикаций ещё не было.</p>
                    ) : (
                      <div className="mt-1.5 flex flex-col gap-1">
                        {history.map((h, i) => <AttemptRow key={i} h={h} />)}
                      </div>
                    )}
                  </div>
                </>
              )}
            </Card>
          </div>
        )}
      </div>
    </>
  )
}

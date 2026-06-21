'use client'
import { useState } from 'react'
import { api } from '@/lib/api'
import type { ReviewAuditResult } from '@/lib/api'

// MVP trigger form: review_id + marketplace → POST /api/reviews/audit.
// review_unavailable is shown as a calm informational note, NOT an error.
// This form never drafts or sends a reply — it only requests a reputation check.

const MARKETPLACES = [
  { v: 'wildberries', l: 'Wildberries' },
  { v: 'ozon', l: 'Ozon' },
  { v: 'yandex', l: 'Яндекс Маркет' },
]

const inp: React.CSSProperties = {
  width: '100%', padding: '8px 10px', fontSize: 12.5, borderRadius: 8,
  border: '1px solid var(--line)', background: 'var(--surface)', color: 'var(--text)',
}
const lab: React.CSSProperties = { fontSize: 11, color: 'var(--text-3)', marginBottom: 4, display: 'block' }

type Outcome =
  | { kind: 'idle' }
  | { kind: 'unavailable' }
  | { kind: 'done'; result: ReviewAuditResult }
  | { kind: 'error'; message: string }

export function ReviewAuditForm(
  { marketplace, onDone }: { marketplace?: string; onDone?: () => void },
) {
  const [reviewId, setReviewId] = useState('')
  const [mp, setMp] = useState(marketplace ?? 'wildberries')
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit() {
    if (!reviewId.trim() || busy) return
    setBusy(true); setOutcome({ kind: 'idle' })
    try {
      const result = await api.reviewAssistant.runReviewAudit(reviewId.trim(), mp)
      if (result.status === 'review_unavailable' || result.ok === false) {
        setOutcome({ kind: 'unavailable' })   // not an error
      } else {
        setOutcome({ kind: 'done', result })
        onDone?.()
      }
    } catch (e) {
      setOutcome({ kind: 'error', message: e instanceof Error ? e.message : 'Не удалось выполнить проверку' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 12,
      padding: 14, display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      <div>
        <label style={lab}>ID отзыва</label>
        <input style={inp} value={reviewId} onChange={(e) => setReviewId(e.target.value)}
          placeholder="например, 1f3c…" />
      </div>
      <div>
        <label style={lab}>Маркетплейс</label>
        <select style={inp} value={mp} onChange={(e) => setMp(e.target.value)}>
          {MARKETPLACES.map((m) => <option key={m.v} value={m.v}>{m.l}</option>)}
        </select>
      </div>
      <button onClick={submit} disabled={busy || !reviewId.trim()} style={{
        alignSelf: 'flex-start', padding: '8px 14px', fontSize: 12.5, fontWeight: 600,
        borderRadius: 8, border: '1px solid var(--line)',
        background: busy ? 'var(--surface)' : 'var(--surface-h)',
        color: 'var(--text)', cursor: busy || !reviewId.trim() ? 'default' : 'pointer',
        opacity: busy || !reviewId.trim() ? 0.6 : 1,
      }}>{busy ? 'Проверяем…' : 'Проверить отзыв'}</button>

      {outcome.kind === 'unavailable' && (
        <div style={{ fontSize: 12, color: 'var(--text-3)' }}>Отзыв не найден или недоступен.</div>
      )}
      {outcome.kind === 'done' && (
        <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
          Проверка выполнена. Найдено вопросов: {outcome.result.total_problems ?? 0}.
        </div>
      )}
      {outcome.kind === 'error' && (
        <div style={{ fontSize: 12, color: 'var(--danger)' }}>Ошибка: {outcome.message}</div>
      )}
    </div>
  )
}

export default ReviewAuditForm

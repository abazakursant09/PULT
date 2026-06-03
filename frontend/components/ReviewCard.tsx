'use client'

import { useState } from 'react'
import { Star, Edit2, Check, X, Scale } from 'lucide-react'
import { type ReviewResponse, type LegalCase } from '@/lib/api'

interface ReviewCardProps {
  review: ReviewResponse
  legalCase?: LegalCase
  onPublish: (id: string) => Promise<void>
  onSkip:    (id: string) => Promise<void>
  onEdit:    (id: string, text: string) => Promise<void>
  onPublishWithText?: (id: string, text: string) => Promise<void>
  onIgnoreLegal?: (caseId: string) => Promise<void>
}

const STATUS_META: Record<string, { bg: string; color: string; label: string }> = {
  pending:   { bg: 'rgba(217,119,6,0.09)',    color: '#D97706', label: 'Ожидает'     },
  approved:  { bg: 'rgba(26,115,232,0.10)',  color: '#1A73E8', label: 'Одобрен'     },
  published: { bg: 'rgba(26,115,232,0.12)',  color: '#1A73E8', label: 'Опубликован' },
  skipped:   { bg: '#F1F3F4',      color: '#8A8986', label: 'Пропущен'    },
}

const LEGAL_REFS: Record<string, string> = {
  high:   'ФЗ «О защите прав потребителей» (ст. 18, 29), ГК РФ ст. 475, 476',
  medium: 'ФЗ-2300-1 ст. 10, ГК РФ ст. 469',
  low:    'ФЗ «О защите прав потребителей» ст. 25',
}

const LEGAL_SITUATION: Record<string, string> = {
  high:   'В тексте отзыва обнаружены формулировки, указывающие на возможные претензии потребителя в рамках ФЗ «О защите прав потребителей». Покупатель может обратиться в Роспотребнадзор или суд.',
  medium: 'Отзыв содержит жалобу на качество или несоответствие товара. Риск репутационного ущерба. Возможны повторные претензии покупателя.',
  low:    'Отзыв содержит замечания субъективного характера без признаков юридических претензий. Влияет на рейтинг, но не создаёт правовых рисков.',
}

const RISK_META: Record<string, { bg: string; color: string; border: string; dot: string; label: string }> = {
  high:   { bg: 'rgba(220,38,38,0.07)',  color: '#DC2626', border: 'rgba(220,38,38,0.2)',   dot: '🔴', label: 'Высокий' },
  medium: { bg: 'rgba(26,115,232,0.08)', color: '#1A73E8', border: 'rgba(26,115,232,0.22)', dot: '🟡', label: 'Средний' },
  low:    { bg: 'rgba(26,115,232,0.06)', color: '#8A8986', border: 'rgba(26,115,232,0.15)', dot: '🟡', label: 'Низкий'  },
}

function extractDraft(aiRec: string): string {
  const match = aiRec.match(/Шаблон[:\s]*[«"]([\s\S]+?)[»"]/)

  if (match) return match[1].trim()
  return aiRec.split('\n').find(l => !l.startsWith('Шаблон') && l.trim().length > 30) ?? aiRec
}

function Stars({ rating }: { rating: number | null }) {
  if (rating === null) return null
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map(i => (
        <Star key={i} size={11}
              style={{ color: i <= rating ? '#3B82F6' : '#D0CCC8', fill: i <= rating ? '#3B82F6' : '#D0CCC8' }} />
      ))}
    </div>
  )
}

export function ReviewCard({ review, legalCase, onPublish, onSkip, onEdit, onPublishWithText, onIgnoreLegal }: ReviewCardProps) {
  const [editing, setEditing] = useState(false)
  const [draft,   setDraft]   = useState(review.response_text ?? '')
  const [loading, setLoading] = useState(false)

  const meta      = STATUS_META[review.status] ?? STATUS_META.pending
  const canAct    = review.status !== 'skipped' && review.status !== 'published'
  const isSkipped = review.status === 'skipped'
  const hasLegal  = isSkipped && !!legalCase && legalCase.status === 'open'

  // Derived from legal case
  const risk      = legalCase ? (RISK_META[legalCase.risk_level] ?? RISK_META.low) : null
  const legalDraft = legalCase ? extractDraft(legalCase.ai_recommendation) : ''
  const [legalEditDraft, setLegalEditDraft] = useState(legalDraft)
  const [legalEditing, setLegalEditing]     = useState(false)

  async function handlePublish() {
    setLoading(true); try { await onPublish(review.id) } finally { setLoading(false) }
  }
  async function handleSkip() {
    setLoading(true); try { await onSkip(review.id) } finally { setLoading(false) }
  }
  async function handleSaveEdit() {
    setLoading(true)
    try { await onEdit(review.id, draft); setEditing(false) } finally { setLoading(false) }
  }
  async function handlePublishLegal() {
    if (!onPublishWithText) return
    setLoading(true)
    try { await onPublishWithText(review.id, legalEditDraft) } finally { setLoading(false) }
  }
  async function handleSaveLegalEdit() {
    if (!onPublishWithText) return
    setLoading(true)
    try { await onPublishWithText(review.id, legalEditDraft); setLegalEditing(false) } finally { setLoading(false) }
  }
  async function handleIgnoreLegal() {
    if (!onIgnoreLegal || !legalCase) return
    setLoading(true)
    try { await onIgnoreLegal(legalCase.id) } finally { setLoading(false) }
  }

  const RATING_ONLY_TEMPLATE = 'Спасибо за вашу оценку! Будем рады видеть вас снова.'
  const isRatingOnly = !review.review_text

  function handleReply() {
    setDraft(review.response_text ?? RATING_ONLY_TEMPLATE)
    setEditing(true)
  }

  return (
    <div
      className="p-5 flex flex-col gap-4 rounded-2xl"
      style={{
        background: isSkipped ? 'rgba(26,115,232,0.04)' : '#F1F3F4',
        border:     isSkipped ? '1px solid rgba(26,115,232,0.22)' : '1px solid rgba(26,115,232,0.1)',
        borderLeft: isSkipped ? '3px solid rgba(26,115,232,0.55)' : '1px solid rgba(26,115,232,0.1)',
      }}
    >
      {/* Negative/problematic banner */}
      {isSkipped && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold"
          style={{ background: 'rgba(26,115,232,0.08)', color: '#1A73E8', border: '1px solid rgba(26,115,232,0.18)' }}
        >
          <Scale size={12} />
          Отзыв 1–2 звезды — передан в Юридический щит
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-semibold text-sm" style={{ color: '#202124' }}>
            {review.author ?? 'Аноним'}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <Stars rating={review.rating} />
            {review.rating !== null && (
              <span className="text-[11px]" style={{ color: 'rgba(0,0,0,0.38)' }}>{review.rating}/5</span>
            )}
          </div>
        </div>
        <span className="text-[11px] font-semibold px-2.5 py-0.5 rounded-md shrink-0"
              style={{ background: meta.bg, color: meta.color }}>
          {meta.label}
        </span>
      </div>

      {/* Review text */}
      <p className="text-sm leading-relaxed pl-3"
         style={{ color: '#8A8986', borderLeft: '2px solid rgba(26,115,232,0.18)' }}>
        {review.review_text ?? (
          <span className="italic" style={{ color: 'rgba(0,0,0,0.38)' }}>Покупатель оставил оценку без комментария</span>
        )}
      </p>

      {/* Legal review block — skipped WITH open legal case */}
      {hasLegal && risk && (
        <div className="flex flex-col gap-3">
          {/* Section header */}
          <div className="flex items-center gap-2">
            <Scale size={13} style={{ color: '#1A73E8' }} />
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: '#1A73E8' }}>
              Юридическая проверка проведена
            </span>
          </div>

          {/* Risk badge */}
          <div
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold"
            style={{ background: risk.bg, color: risk.color, border: `1px solid ${risk.border}` }}
          >
            <span>🎯 Оценка риска: {risk.dot} {risk.label}</span>
          </div>

          {/* Legal description */}
          <div
            className="rounded-xl px-3 py-2.5 space-y-1.5"
            style={{ background: '#F1F3F4', border: '1px solid rgba(26,115,232,0.1)' }}
          >
            <p className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'rgba(0,0,0,0.38)' }}>
              ⚖️ Описание ситуации с точки зрения права
            </p>
            <p className="text-xs leading-relaxed" style={{ color: '#5F6368' }}>
              {LEGAL_SITUATION[legalCase!.risk_level] ?? LEGAL_SITUATION.low}
            </p>
            <p className="text-[11px] mt-1" style={{ color: 'rgba(0,0,0,0.30)' }}>
              {LEGAL_REFS[legalCase!.risk_level] ?? LEGAL_REFS.low}
            </p>
          </div>

          {/* Recommended response */}
          <div className="space-y-1.5">
            <p className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'rgba(0,0,0,0.38)' }}>
              📝 Рекомендуемый ответ
            </p>
            {legalEditing ? (
              <div className="flex flex-col gap-2">
                <textarea
                  value={legalEditDraft}
                  onChange={e => setLegalEditDraft(e.target.value)}
                  rows={4}
                  className="w-full rounded-xl px-3 py-2.5 text-sm resize-none"
                  style={{
                    background: '#F1F3F4',
                    border: '1px solid rgba(26,115,232,0.25)',
                    outline: 'none',
                    color: '#202124',
                    fontFamily: 'var(--font-inter)',
                    lineHeight: 1.6,
                  }}
                />
                <div className="flex gap-2">
                  <button onClick={handleSaveLegalEdit} disabled={loading} className="btn btn-primary"
                          style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
                    <Check size={12} /> Сохранить и опубликовать
                  </button>
                  <button
                    onClick={() => { setLegalEditing(false); setLegalEditDraft(legalDraft) }}
                    className="btn btn-ghost" style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
                    <X size={12} /> Отмена
                  </button>
                </div>
              </div>
            ) : (
              <p className="text-sm leading-relaxed" style={{ color: '#8A8986' }}>
                {legalEditDraft}
              </p>
            )}
          </div>

          {/* Action buttons */}
          {!legalEditing && (
            <div className="flex flex-wrap items-center gap-2 pt-2"
                 style={{ borderTop: '1px solid rgba(26,115,232,0.1)' }}>
              <button
                onClick={handlePublishLegal}
                disabled={loading || !onPublishWithText}
                className="btn btn-primary flex-1 sm:flex-none"
                style={{ padding: '7px 14px', fontSize: '0.78rem' }}
              >
                <Check size={12} /> ✅ Опубликовать
              </button>
              <button
                onClick={() => setLegalEditing(true)}
                disabled={loading}
                className="btn btn-ghost flex-1 sm:flex-none"
                style={{ padding: '7px 14px', fontSize: '0.78rem' }}
              >
                <Edit2 size={12} /> ✏️ Редактировать
              </button>
              <button
                onClick={handleIgnoreLegal}
                disabled={loading || !onIgnoreLegal}
                className="btn btn-ghost flex-1 sm:flex-none"
                style={{ padding: '7px 14px', fontSize: '0.78rem', opacity: 0.7 }}
              >
                <X size={12} /> 🚫 Игнорировать
              </button>
            </div>
          )}
        </div>
      )}

      {/* Skipped WITHOUT legal case */}
      {isSkipped && !legalCase && (
        <p className="text-[12px] italic" style={{ color: 'rgba(0,0,0,0.38)' }}>
          Ответ не генерируется для этого отзыва
        </p>
      )}

      {/* Standard response block for non-skipped */}
      {!isSkipped && (
        <div className="flex flex-col gap-2">
          <span className="label">Ответ продавца</span>

          {editing ? (
            <div className="flex flex-col gap-2">
              <textarea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                rows={4}
                className="w-full rounded-xl px-3 py-2.5 text-sm resize-none"
                style={{
                  background: '#F1F3F4',
                  border: '1px solid rgba(26,115,232,0.25)',
                  outline: 'none',
                  color: '#202124',
                  fontFamily: 'var(--font-inter)',
                  lineHeight: 1.6,
                }}
              />
              <div className="flex gap-2">
                <button onClick={handleSaveEdit} disabled={loading} className="btn btn-primary"
                        style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
                  <Check size={12} /> Сохранить
                </button>
                <button
                  onClick={() => { setEditing(false); setDraft(review.response_text ?? '') }}
                  className="btn btn-ghost" style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
                  <X size={12} /> Отмена
                </button>
              </div>
            </div>
          ) : (
            <p className="text-sm leading-relaxed" style={{ color: '#8A8986' }}>
              {review.response_text ?? (
                <span className="italic" style={{ color: 'rgba(0,0,0,0.38)' }}>Ответ не сгенерирован</span>
              )}
            </p>
          )}
        </div>
      )}

      {/* Standard actions for non-skipped */}
      {canAct && !editing && (
        <div className="flex flex-wrap items-center gap-2 pt-3"
             style={{ borderTop: '1px solid rgba(26,115,232,0.1)' }}>
          <button onClick={handlePublish} disabled={loading} className="btn btn-primary flex-1 sm:flex-none"
                  style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
            <Check size={12} /> Опубликовать
          </button>
          {isRatingOnly ? (
            <button onClick={handleReply} disabled={loading} className="btn btn-ghost flex-1 sm:flex-none"
                    style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
              <Edit2 size={12} /> Ответить
            </button>
          ) : (
            <button onClick={() => setEditing(true)} disabled={loading} className="btn btn-ghost flex-1 sm:flex-none"
                    style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
              <Edit2 size={12} /> Редактировать
            </button>
          )}
          <button onClick={handleSkip} disabled={loading} className="btn btn-ghost flex-1 sm:flex-none"
                  style={{ padding: '7px 14px', fontSize: '0.78rem' }}>
            <X size={12} /> Пропустить
          </button>
        </div>
      )}
    </div>
  )
}

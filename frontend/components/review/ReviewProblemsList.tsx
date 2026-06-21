'use client'
import type { ReviewProblem } from '@/lib/api'

const SEVERITY_RU: Record<string, string> = {
  critical: 'Критично', high: 'Высокий', medium: 'Средний', low: 'Низкий',
}
const CATEGORY_RU: Record<string, string> = {
  RISK: 'Риск для репутации', ATTENTION: 'Требует внимания', SAFE: 'Безопасно',
}
const PROBLEM_RU: Record<string, string> = {
  unanswered_negative_review: 'Негатив без ответа',
  unanswered_attention_review: 'Спорный отзыв без ответа',
  safe_review_can_reply: 'Положительный отзыв без ответа',
  five_star_without_text: '5★ без текста и ответа',
  complaint_detected: 'Признаки жалобы',
  already_answered: 'Отзыв уже обработан',
}

function sevColor(sev: string) {
  return sev === 'critical' ? 'var(--danger)' : sev === 'high' ? 'var(--text)' : 'var(--text-3)'
}

export function ReviewProblemsList({ problems }: { problems: ReviewProblem[] }) {
  if (!problems.length) {
    return (
      <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'center', padding: '14px 0' }}>
        В последней проверке проблем не обнаружено.
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {problems.map((p, i) => (
        <div key={`${p.review_id ?? 'r'}:${p.problem_type}:${i}`} style={{
          background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 10, padding: 12,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
              {PROBLEM_RU[p.problem_type] ?? p.problem_type}
            </span>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: sevColor(p.severity) }}>
              {SEVERITY_RU[p.severity] ?? p.severity}
            </span>
            {p.category && (
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{CATEGORY_RU[p.category] ?? p.category}</span>
            )}
          </div>
          {p.review_id && <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 4 }}>Отзыв: {p.review_id}</div>}
          {p.evidence && Object.keys(p.evidence).length > 0 && (
            <pre style={{
              fontSize: 10.5, color: 'var(--text-3)', marginTop: 6, marginBottom: 0,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit',
            }}>{JSON.stringify(p.evidence, null, 0)}</pre>
          )}
        </div>
      ))}
    </div>
  )
}

export default ReviewProblemsList

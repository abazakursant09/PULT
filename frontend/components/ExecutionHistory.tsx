'use client'

/**
 * Execution History (ME-6.1). Answers "What did PULT do for me?" — not "what
 * did PULT detect?". Source of truth: GET /api/executions (+ /executions/{id}
 * for the details drawer). UI renders backend records only.
 */
import { useCallback, useEffect, useState } from 'react'
import { api, type ExecutionLogItem, type ExecutionLogDetail } from '@/lib/api'
import { T } from '@/lib/tokens'

const C = {
  surf: T.surf, line: T.line, text: T.text, text2: T.text2, text3: T.text3, v: T.v,
  ok: T.ok, warn: T.warn, red: T.red,
}

const STATUS: Record<string, { label: string; color: string }> = {
  success:  { label: 'выполнено',  color: C.ok },
  pending:  { label: 'в процессе', color: C.warn },
  failed:   { label: 'ошибка',     color: C.red },
  rejected: { label: 'отклонено',  color: C.red },
  reverted: { label: 'откат',      color: C.text3 },
  dry_run:  { label: 'проверка',   color: C.text3 },
}

const ACTION_LABEL: Record<string, string> = {
  publish_review_response: 'Публикация ответа на отзыв',
  set_price:               'Изменение цены',
  ad_set_bid:              'Изменение ставки рекламы',
  ad_set_state:            'Пауза/запуск кампании',
  update_card:             'Обновление SEO-карточки',
}

function fmt(ts: string): string {
  // backend timestamps are naive UTC; render as-is, trimmed
  return ts.replace('T', ' ').slice(0, 19)
}

export default function ExecutionHistory() {
  const [rows, setRows] = useState<ExecutionLogItem[]>([])
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ExecutionLogDetail | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.executions.list().then(setRows).catch(() => setRows([])).finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const openDetail = useCallback((id: string) => {
    setOpenId(id); setDetail(null)
    api.executions.detail(id).then(setDetail).catch(() => setDetail(null))
  }, [])

  return (
    <div style={{ background: C.surf, border: `1px solid ${C.line}`, borderRadius: 10, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Что ПУЛЬТ сделал за меня</h3>
        <button onClick={load} style={{ fontSize: 11, color: C.text2, background: 'transparent', border: `1px solid ${C.line}`, borderRadius: 6, padding: '4px 10px', cursor: 'pointer' }}>
          Обновить
        </button>
      </div>

      {loading ? (
        <p style={{ fontSize: 12.5, color: C.text3 }}>Загрузка…</p>
      ) : rows.length === 0 ? (
        <p style={{ fontSize: 12.5, color: C.text3, lineHeight: 1.5 }}>
          Пока ничего не выполнено. Откройте инсайт и нажмите «Выполнить» — действие появится здесь.
        </p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {rows.map(r => {
            const st = STATUS[r.status] ?? { label: r.status, color: C.text3 }
            return (
              <button key={r.id} onClick={() => openDetail(r.id)} style={{
                display: 'grid', gridTemplateColumns: '120px 1fr auto', gap: 10, alignItems: 'center',
                textAlign: 'left', padding: '8px 10px', borderRadius: 6, cursor: 'pointer',
                background: 'rgba(255,255,255,0.015)', border: `1px solid ${C.line}`, color: C.text,
              }}>
                <span style={{ fontSize: 10.5, color: C.text3 }}>{fmt(r.created_at)}</span>
                <span style={{ fontSize: 12.5, color: C.text }}>
                  {ACTION_LABEL[r.action_type] ?? r.action_type}
                  {r.insight_key && <span style={{ color: C.text3 }}> · {r.insight_key.split(':')[0]}</span>}
                </span>
                <span style={{ fontSize: 10.5, fontWeight: 700, color: st.color }}>{st.label}</span>
              </button>
            )
          })}
        </div>
      )}

      {/* Details drawer */}
      {openId && (
        <div onClick={() => setOpenId(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 60,
          display: 'flex', justifyContent: 'flex-end',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: 'min(480px, 92vw)', height: '100%', background: '#161618',
            borderLeft: `1px solid ${C.line}`, padding: 20, overflowY: 'auto',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14 }}>
              <h4 style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Детали выполнения</h4>
              <button onClick={() => setOpenId(null)} style={{ background: 'transparent', border: 'none', color: C.text2, cursor: 'pointer', fontSize: 18 }}>×</button>
            </div>
            {!detail ? <p style={{ color: C.text3, fontSize: 12.5 }}>Загрузка…</p> : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <Row k="execution_id" v={detail.id} />
                <Row k="insight_key" v={detail.insight_key ?? '—'} />
                <Row k="action_type" v={detail.action_type} />
                <Row k="mode" v={detail.mode} />
                <Row k="status" v={detail.status} />
                <Row k="marketplace" v={detail.marketplace ?? '—'} />
                <Row k="timestamp" v={fmt(detail.created_at)} />
                <Block k="payload" v={detail.payload} />
                <Block k="marketplace_response" v={detail.result} />
                {detail.error_code && <Row k="error" v={detail.error_code} />}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: 'flex', gap: 10 }}>
      <span style={{ fontSize: 10.5, color: C.text3, width: 140, flexShrink: 0, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k}</span>
      <span style={{ fontSize: 12, color: C.text, wordBreak: 'break-all' }}>{v}</span>
    </div>
  )
}

function Block({ k, v }: { k: string; v: unknown }) {
  return (
    <div>
      <span style={{ fontSize: 10.5, color: C.text3, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k}</span>
      <pre style={{
        margin: '4px 0 0', padding: '8px 10px', borderRadius: 6, background: '#0E0E10',
        border: `1px solid ${C.line}`, fontSize: 11, color: C.text2, overflowX: 'auto', lineHeight: 1.5,
      }}>{v ? JSON.stringify(v, null, 2) : '—'}</pre>
    </div>
  )
}

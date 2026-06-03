'use client'

import { useState, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  Upload, FileText, CheckCircle2, AlertTriangle, X,
  ChevronDown, Download, ArrowRight, RefreshCw,
} from 'lucide-react'
import { api } from '@/lib/api'
import { trackEvent, stampFunnel, elapsedSince, firstTimeOnly, FUNNEL_TS } from '@/lib/events'
import { T } from '@/lib/tokens'
import { ErrorState } from '@/components/system/ErrorState'

// ── Types ─────────────────────────────────────────────────────────────────────
type MP   = 'wb' | 'ozon' | 'ym' | ''
type IType = 'finance' | 'products' | ''
type Stage = 'upload' | 'preview' | 'importing' | 'done' | 'error'

interface PreviewData {
  import_id:          string
  marketplace:        string | null
  import_type:        string | null
  total_rows:         number
  valid_rows:         number
  skipped_rows:       number
  headers:            string[]
  mapped_columns:     Record<string, string>
  unmapped_required:  string[]
  preview_rows:       Record<string, unknown>[]
  warnings:           string[]
  errors:             string[]
  duplicate_import_id: string | null
  duplicate_date:      string | null
}

// ── Consts ────────────────────────────────────────────────────────────────────
const MP_LABELS: Record<string, string> = {
  wb: 'Wildberries', ozon: 'Ozon', ym: 'Яндекс Маркет',
}
const TYPE_LABELS: Record<string, string> = {
  finance: 'Финансы', products: 'Товары',
}
const MP_COLORS: Record<string, string> = {
  wb:   '#CB11AB',
  ozon: '#005BFF',
  ym:   '#FFCC01',
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtBytes(b: number) {
  if (b < 1024)       return `${b} Б`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} КБ`
  return `${(b / 1024 / 1024).toFixed(1)} МБ`
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function ImportPage() {
  const router = useRouter()

  const [stage,     setStage]     = useState<Stage>('upload')
  const [dragging,  setDragging]  = useState(false)
  const [file,      setFile]      = useState<File | null>(null)
  const [mp,        setMp]        = useState<MP>('')
  const [itype,     setIType]     = useState<IType>('')
  const [uploading,   setUploading]   = useState(false)
  const [slowImport,  setSlowImport]  = useState(false)
  const [preview,     setPreview]     = useState<PreviewData | null>(null)
  const [dupAction, setDupAction] = useState<'overwrite' | 'skip' | 'new' | null>(null)
  const [result,    setResult]    = useState<{ imported: number; skipped: number } | null>(null)
  const [error,     setError]     = useState('')

  const fileRef = useRef<HTMLInputElement>(null)

  // ── Drag & drop ─────────────────────────────────────────────────────────────
  const onDragOver  = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragging(true) }, [])
  const onDragLeave = useCallback(() => setDragging(false), [])
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f?.name.endsWith('.csv')) { setFile(f); setError('') }
    else setError('Поддерживаются только .csv файлы')
  }, [])
  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) { setFile(f); setError('') }
  }

  // ── Upload & parse ───────────────────────────────────────────────────────────
  async function handleUpload() {
    if (!file) return
    setUploading(true); setError('')
    trackEvent('import_started', 'import', undefined, { marketplace: mp, import_type: itype })
    try {
      const data = await api.csvImport.upload(file, mp, itype)
      setPreview(data)
      setStage('preview')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка при загрузке файла')
    } finally {
      setUploading(false)
    }
  }

  // ── Confirm ──────────────────────────────────────────────────────────────────
  async function handleConfirm() {
    if (!preview) return
    setStage('importing')
    setSlowImport(false)
    const slowTimer = setTimeout(() => { setSlowImport(true); trackEvent('import_timeout_seen', 'import') }, 30_000)
    try {
      const data = await api.csvImport.confirm(preview.import_id)
      setResult({ imported: data.imported_count, skipped: data.skipped_count })
      setStage('done')
      stampFunnel(FUNNEL_TS.firstImport)
      // time_to_first_import — computed automatically, only on the genuine first import
      const timeToFirstImportMs = firstTimeOnly('bp_evt_first_import') ? elapsedSince(FUNNEL_TS.signup) : undefined
      trackEvent('import_completed', 'import', preview.import_id, {
        imported: data.imported_count,
        skipped: data.skipped_count,
        time_to_first_import_ms: timeToFirstImportMs,
      })
    } catch (e: unknown) {
      const isTimeout = e instanceof Error && (e.name === 'AbortError' || e.message.toLowerCase().includes('timeout') || e.message.toLowerCase().includes('abort'))
      setError(isTimeout
        ? 'Обработка заняла слишком много времени. Файл можно загрузить повторно.'
        : (e instanceof Error ? e.message : 'Ошибка при импорте'))
      setStage('error')
      trackEvent(isTimeout ? 'upload_timeout_seen' : 'import_failed', 'import', preview.import_id)
    } finally {
      clearTimeout(slowTimer)
      setSlowImport(false)
    }
  }

  // ── Reset ────────────────────────────────────────────────────────────────────
  function reset() {
    setStage('upload'); setFile(null); setPreview(null)
    setResult(null); setError(''); setDupAction(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const effectiveMp    = (preview?.marketplace ?? mp) as string
  const effectiveIType = (preview?.import_type ?? itype) as string
  const hasErrors      = (preview?.errors?.length ?? 0) > 0
  const hasDup         = Boolean(preview?.duplicate_import_id)

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div style={{ padding: T.layout.padding, maxWidth: T.layout.maxWidth, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: T.sz.pageTitle, fontWeight: 700, color: T.text, letterSpacing: '-0.02em', lineHeight: 1, marginBottom: 6 }}>
          Импорт данных
        </h1>
        <p style={{ fontSize: 14, color: '#8E8E93' }}>
          Загрузите CSV из Wildberries, Ozon или Яндекс Маркета — данные появятся в Финансах и Пульте
        </p>
      </div>

      {/* Template downloads */}
      <div className="flex flex-wrap gap-2 mb-6">
        {(['wb','ozon','ym'] as const).map(m => (
          ['finance','products'].map(t => (
            <a
              key={`${m}-${t}`}
              href={`${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/import/templates/${m}/${t}`}
              download
              style={{
                fontSize: 11, padding: '4px 10px',
                background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 6, color: '#8E8E93', textDecoration: 'none',
                display: 'flex', alignItems: 'center', gap: 4,
              }}
            >
              <Download size={10} />
              {MP_LABELS[m]} / {TYPE_LABELS[t]}
            </a>
          ))
        ))}
      </div>

      {/* ── STAGE: UPLOAD ── */}
      {stage === 'upload' && (
        <div className="space-y-4">
          {/* Drop zone */}
          <div
            onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            style={{
              border: `2px dashed ${dragging ? '#6E6AFC' : 'rgba(255,255,255,0.12)'}`,
              borderRadius: 12, padding: '40px 24px',
              background: dragging ? 'rgba(110,106,252,0.06)' : 'rgba(255,255,255,0.02)',
              cursor: 'pointer', textAlign: 'center',
              transition: 'all 0.15s ease',
            }}
          >
            <input ref={fileRef} type="file" accept=".csv" hidden onChange={onFileChange} />
            <div style={{
              width: 48, height: 48, borderRadius: 12,
              background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.20)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              margin: '0 auto 16px',
            }}>
              <Upload size={20} style={{ color: '#6E6AFC' }} />
            </div>
            {file ? (
              <div>
                <p style={{ fontSize: 15, fontWeight: 600, color: '#EDEDF0', marginBottom: 4 }}>
                  <FileText size={14} style={{ display: 'inline', marginRight: 6, color: '#6E6AFC' }} />
                  {file.name}
                </p>
                <p style={{ fontSize: 12, color: '#8E8E93' }}>{fmtBytes(file.size)}</p>
              </div>
            ) : (
              <div>
                <p style={{ fontSize: 15, fontWeight: 500, color: '#EDEDF0', marginBottom: 6 }}>
                  Перетащите CSV сюда или нажмите для выбора
                </p>
                <p style={{ fontSize: 12, color: '#6B6B72' }}>Только .csv, максимум 10 МБ</p>
              </div>
            )}
          </div>

          {/* Selectors */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label mb-2">МАРКЕТПЛЕЙС</label>
              <div style={{ position: 'relative' }}>
                <select
                  value={mp}
                  onChange={e => setMp(e.target.value as MP)}
                  className="input"
                  style={{ width: '100%', paddingRight: 32, appearance: 'none' }}
                >
                  <option value="">Определить автоматически</option>
                  <option value="wb">Wildberries</option>
                  <option value="ozon">Ozon</option>
                  <option value="ym">Яндекс Маркет</option>
                </select>
                <ChevronDown size={14} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', color: '#8E8E93', pointerEvents: 'none' }} />
              </div>
            </div>
            <div>
              <label className="label mb-2">ТИП ДАННЫХ</label>
              <div style={{ position: 'relative' }}>
                <select
                  value={itype}
                  onChange={e => setIType(e.target.value as IType)}
                  className="input"
                  style={{ width: '100%', paddingRight: 32, appearance: 'none' }}
                >
                  <option value="">Определить автоматически</option>
                  <option value="finance">Финансы</option>
                  <option value="products">Товары</option>
                </select>
                <ChevronDown size={14} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', color: '#8E8E93', pointerEvents: 'none' }} />
              </div>
            </div>
          </div>

          {error && (
            <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 8, padding: '10px 14px', color: '#FCA5A5', fontSize: 13 }}>
              {error}
            </div>
          )}

          <button
            className="btn btn-primary"
            style={{ width: '100%' }}
            disabled={!file || uploading}
            onClick={handleUpload}
          >
            {uploading
              ? <><span className="spinner" style={{ marginRight: 8 }} /> Анализируем файл...</>
              : <><Upload size={15} style={{ marginRight: 8 }} /> Загрузить и проверить</>}
          </button>
        </div>
      )}

      {/* ── STAGE: PREVIEW ── */}
      {stage === 'preview' && preview && (
        <div className="space-y-4 animate-fade-in">
          {/* Detection summary */}
          <div style={{ background: '#242428', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 20 }}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                {effectiveMp && (
                  <span style={{
                    fontSize: 11, fontWeight: 700, padding: '3px 10px',
                    borderRadius: 20, background: MP_COLORS[effectiveMp] + '20',
                    color: MP_COLORS[effectiveMp], border: `1px solid ${MP_COLORS[effectiveMp]}40`,
                    textTransform: 'uppercase' as const,
                  }}>
                    {MP_LABELS[effectiveMp] ?? effectiveMp}
                  </span>
                )}
                {effectiveIType && (
                  <span className="badge" style={{ background: 'rgba(110,106,252,0.10)', color: '#A78BFA' }}>
                    {TYPE_LABELS[effectiveIType] ?? effectiveIType}
                  </span>
                )}
              </div>
              <button onClick={reset} style={{ color: '#8E8E93', cursor: 'pointer', background: 'none', border: 'none' }}>
                <X size={16} />
              </button>
            </div>

            <div className="grid grid-cols-3 gap-3">
              {[
                { label: 'СТРОК ВСЕГО', value: preview.total_rows },
                { label: 'КОРРЕКТНЫХ', value: preview.valid_rows, ok: true },
                { label: 'ПРОПУЩЕНО', value: preview.skipped_rows, warn: preview.skipped_rows > 0 },
              ].map(({ label, value, ok, warn }) => (
                <div key={label} style={{ textAlign: 'center', padding: '12px 8px', background: 'rgba(255,255,255,0.03)', borderRadius: 8 }}>
                  <div style={{ fontSize: 24, fontWeight: 700, color: ok ? '#22C55E' : warn ? '#F59E0B' : '#EDEDF0' }}>
                    {value}
                  </div>
                  <div className="label" style={{ marginTop: 4 }}>{label}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Duplicate warning */}
          {hasDup && !dupAction && (
            <div style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 10, padding: 16 }}>
              <p style={{ fontSize: 13, color: '#FCD34D', marginBottom: 12 }}>
                ⚠️ Этот файл уже импортировался {preview.duplicate_date}. Что делать?
              </p>
              <div className="flex gap-2 flex-wrap">
                {[
                  { key: 'overwrite', label: 'Перезаписать' },
                  { key: 'new',       label: 'Импортировать как новый' },
                  { key: 'skip',      label: 'Пропустить' },
                ].map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => {
                      if (key === 'skip') { reset(); return }
                      setDupAction(key as NonNullable<typeof dupAction>)
                    }}
                    style={{
                      fontSize: 12, padding: '6px 14px', borderRadius: 6,
                      background: key === 'skip' ? 'transparent' : 'rgba(245,158,11,0.15)',
                      border: '1px solid rgba(245,158,11,0.25)',
                      color: '#FCD34D', cursor: 'pointer',
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Blocking errors */}
          {hasErrors && (
            <div style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 10, padding: 16 }}>
              <p style={{ fontSize: 13, fontWeight: 600, color: '#FCA5A5', marginBottom: 8 }}>Блокирующие ошибки:</p>
              {preview.errors.map((e, i) => (
                <p key={i} style={{ fontSize: 12, color: '#FCA5A5', marginBottom: 4 }}>• {e}</p>
              ))}
            </div>
          )}

          {/* Warnings (non-blocking) */}
          {preview.warnings.length > 0 && !hasErrors && (
            <div style={{ background: 'rgba(245,158,11,0.05)', border: '1px solid rgba(245,158,11,0.15)', borderRadius: 10, padding: 14 }}>
              <p style={{ fontSize: 12, fontWeight: 600, color: '#FCD34D', marginBottom: 6 }}>
                <AlertTriangle size={12} style={{ display: 'inline', marginRight: 5 }} />
                Предупреждения ({preview.warnings.length}):
              </p>
              {preview.warnings.slice(0, 5).map((w, i) => (
                <p key={i} style={{ fontSize: 11.5, color: '#FCD34D', opacity: 0.8, marginBottom: 3 }}>• {w}</p>
              ))}
              {preview.warnings.length > 5 && (
                <p style={{ fontSize: 11, color: '#6B6B72', marginTop: 4 }}>и ещё {preview.warnings.length - 5} предупреждений...</p>
              )}
            </div>
          )}

          {/* Column mapping */}
          {Object.keys(preview.mapped_columns).length > 0 && (
            <div style={{ background: '#242428', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10, padding: 16 }}>
              <p className="label mb-3">СОПОСТАВЛЕНИЕ КОЛОНОК</p>
              <div className="space-y-1.5">
                {Object.entries(preview.mapped_columns).map(([field, col]) => (
                  <div key={field} className="flex items-center gap-2" style={{ fontSize: 12 }}>
                    <span style={{ color: '#8E8E93', width: 100, flexShrink: 0 }}>{field}</span>
                    <ArrowRight size={10} style={{ color: '#6B6B72', flexShrink: 0 }} />
                    <span style={{ color: col ? '#A78BFA' : '#EF4444' }}>
                      {col || '— не найдено'}
                    </span>
                    {col ? <CheckCircle2 size={11} style={{ color: '#22C55E' }} /> : <X size={11} style={{ color: '#EF4444' }} />}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Preview table */}
          {preview.preview_rows.length > 0 && (
            <div style={{ background: '#242428', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                <p className="label">ПРЕДПРОСМОТР (первые 5 строк)</p>
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr>
                      {Object.keys(preview.preview_rows[0]).map(k => (
                        <th key={k} style={{ padding: '8px 12px', textAlign: 'left', color: '#6B6B72', fontWeight: 500, borderBottom: '1px solid rgba(255,255,255,0.06)', whiteSpace: 'nowrap' }}>
                          {k}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.preview_rows.map((row, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                        {Object.values(row).map((v, j) => (
                          <td key={j} style={{ padding: '7px 12px', color: '#EDEDF0', whiteSpace: 'nowrap' }}>
                            {String(v ?? '—')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex gap-3">
            <button onClick={reset} className="btn btn-ghost" style={{ flex: '0 0 auto' }}>
              ← Назад
            </button>
            <button
              className="btn btn-primary"
              style={{ flex: 1 }}
              disabled={hasErrors || (hasDup && !dupAction)}
              onClick={handleConfirm}
            >
              Импортировать {preview.valid_rows} строк <ArrowRight size={14} style={{ marginLeft: 6 }} />
            </button>
          </div>
        </div>
      )}

      {/* ── STAGE: IMPORTING ── */}
      {stage === 'importing' && (
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <div style={{
            width: 56, height: 56, borderRadius: '50%',
            border: '3px solid rgba(110,106,252,0.2)',
            borderTopColor: '#6E6AFC',
            animation: 'spin 0.8s linear infinite',
            margin: '0 auto 20px',
          }} />
          <p style={{ fontSize: 16, fontWeight: 600, color: '#EDEDF0', marginBottom: 6 }}>Импортируем данные...</p>
          <p style={{ fontSize: 13, color: '#8E8E93' }}>
            {slowImport ? 'Обработка займёт до 90 секунд…' : 'Это займёт несколько секунд'}
          </p>
          <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
        </div>
      )}

      {/* ── STAGE: DONE ── */}
      {stage === 'done' && result && (
        <div style={{ textAlign: 'center', padding: '40px 20px' }} className="animate-fade-in">
          <div style={{
            width: 64, height: 64, borderRadius: '50%',
            background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.25)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px',
          }}>
            <CheckCircle2 size={28} style={{ color: '#22C55E' }} />
          </div>
          <p style={{ fontSize: 20, fontWeight: 700, color: '#EDEDF0', marginBottom: 8 }}>
            Импорт завершён
          </p>
          <p style={{ fontSize: 14, color: '#8E8E93', marginBottom: 4 }}>
            Импортировано <strong style={{ color: '#22C55E' }}>{result.imported}</strong> строк
            {result.skipped > 0 && `, пропущено ${result.skipped}`}
          </p>
          <p style={{ fontSize: 12, color: '#6B6B72', marginBottom: 32 }}>
            Данные доступны в разделах Финансы и Пульт
          </p>

          {/* SEO cards CTA */}
          {(preview?.import_type === 'products' || effectiveIType === 'products') && (
            <div style={{
              background: 'rgba(110,106,252,0.06)', border: '1px solid rgba(110,106,252,0.15)',
              borderRadius: 12, padding: 16, marginBottom: 20, maxWidth: 380, margin: '0 auto 20px',
            }}>
              <p style={{ fontSize: 13, color: '#A78BFA', marginBottom: 10 }}>
                ✨ Создайте SEO-карточки для импортированных товаров
              </p>
              <button
                className="btn btn-primary"
                style={{ width: '100%' }}
                onClick={() => router.push('/dashboard/seo-cards')}
              >
                Открыть SEO-карточки →
              </button>
            </div>
          )}

          <div className="flex gap-3 justify-center">
            <button
              className="btn btn-primary"
              onClick={() => router.push(preview?.import_type === 'products' ? '/dashboard' : '/dashboard/finance')}
            >
              {preview?.import_type === 'products' ? 'Перейти в Пульт' : 'Открыть Финансы'} <ArrowRight size={14} style={{ marginLeft: 6 }} />
            </button>
            <button onClick={reset} className="btn btn-ghost">
              <RefreshCw size={14} style={{ marginRight: 6 }} /> Ещё импорт
            </button>
          </div>
        </div>
      )}

      {/* ── STAGE: ERROR ── */}
      {stage === 'error' && (
        <ErrorState
          message={error || 'Не удалось выполнить импорт'}
          onRetry={reset}
          retryLabel="Загрузить снова"
          paddingTop={48}
        />
      )}
    </div>
  )
}

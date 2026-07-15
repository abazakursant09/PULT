'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  Upload, FileText, CheckCircle2, AlertTriangle, X,
  ChevronDown, Download, ArrowRight, RefreshCw,
} from 'lucide-react'
import { api } from '@/lib/api'
import { trackEvent, stampFunnel, elapsedSince, firstTimeOnly, FUNNEL_TS } from '@/lib/events'
import { SellerBar } from '@/components/seller/Shell'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/system/ErrorState'

// Import experience (P4 premium redesign). Presentation only — the same api.csvImport calls, the
// same funnel events, the same CSV / duplicate / finance-first behaviour. All colour is on P0
// tokens, all surfaces are P1 components; no legacy `T` tokens, no raw hex, no old-violet accent,
// no `.btn`/`.input`/`.badge` classes, no bespoke spinner.

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

// Minimal progress signal — the four committed stages, so the seller sees where they are.
const STEPS: { key: Stage; label: string }[] = [
  { key: 'upload', label: 'Загрузка' },
  { key: 'preview', label: 'Проверка' },
  { key: 'importing', label: 'Импорт' },
  { key: 'done', label: 'Готово' },
]
function stepIndex(s: Stage): number {
  const i = STEPS.findIndex(x => x.key === s)
  return i === -1 ? 0 : i   // 'error' maps to 0 (it renders its own panel)
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtBytes(b: number) {
  if (b < 1024)       return `${b} Б`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} КБ`
  return `${(b / 1024 / 1024).toFixed(1)} МБ`
}

const SELECT_CLASS =
  'w-full appearance-none rounded-[var(--r-sm)] border border-[var(--line)] bg-[var(--bg)] text-[var(--text)] text-[14px] h-[44px] pl-3 pr-8 cursor-pointer transition-[border-color] duration-[var(--dur)] focus-visible:outline-none focus-visible:border-[var(--violet-text)] disabled:opacity-40 disabled:cursor-not-allowed'
const FIELD_LABEL = 'block text-[11px] font-semibold uppercase tracking-wide text-[var(--text-3)] mb-2'

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
  // null = still loading. The Business Diagnosis is built from the FINANCIAL report: the Advisory
  // Runtime only considers a seller who has imported finance rows, so a first upload of "Товары"
  // produces no diagnosis. Rather than change the Runtime, the UI asks for the financial report
  // first and says why. Behaviour unchanged from before — only restyled.
  const [hasFinance, setHasFinance] = useState<boolean | null>(null)

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

  useEffect(() => {
    let alive = true
    api.csvImport.history()
      .then((rows) => {
        if (alive) setHasFinance(rows.some(r => r.import_type === 'finance' && r.status === 'confirmed'))
      })
      // Fail OPEN: if we cannot read the history we must not lock a seller out of importing.
      .catch(() => { if (alive) setHasFinance(true) })
    return () => { alive = false }
  }, [])

  const financeFirst = hasFinance === false      // first import must be the financial report

  // ── Upload & parse ───────────────────────────────────────────────────────────
  async function handleUpload() {
    if (!file) return
    setUploading(true); setError('')
    trackEvent('import_started', 'import', undefined, { marketplace: mp, import_type: itype })
    try {
      const data = await api.csvImport.upload(file, mp, financeFirst ? 'finance' : itype)
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
    // Belt and braces: the selector is locked, but the parser also auto-detects a type. A first
    // import that is not the financial report would import cleanly and then produce no diagnosis,
    // so it is refused here rather than silently accepted.
    if (financeFirst && preview.import_type && preview.import_type !== 'finance') {
      setError('Первым нужно загрузить финансовый отчёт — по нему строится диагноз. Отчёт по товарам можно будет загрузить сразу после него.')
      return
    }
    setStage('importing')
    setSlowImport(false)
    const slowTimer = setTimeout(() => { setSlowImport(true); trackEvent('import_timeout_seen', 'import') }, 30_000)
    try {
      // "overwrite" replaces the prior copy of this same file server-side; "new" (and the
      // no-duplicate case) appends. Anything else means no duplicate was flagged → append.
      const data = await api.csvImport.confirm(
        preview.import_id, dupAction === 'overwrite' ? 'overwrite' : 'new')
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
  const curStep        = stepIndex(stage)

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <>
      <SellerBar title="Импорт данных" sub="Загрузите CSV — диагноз появится на главной в течение минуты" />
      <div className="s-canvas" style={{ maxWidth: 960 }}>

        {/* Progress signal — where the seller is in the flow */}
        {stage !== 'error' && (
          <div className="flex items-center gap-2 mb-6">
            {STEPS.map((s, i) => (
              <div key={s.key} className="flex items-center gap-2">
                <div className="flex items-center gap-1.5">
                  <span
                    className="h-1.5 w-1.5 rounded-full transition-colors duration-[var(--dur)]"
                    style={{ background: i <= curStep ? 'var(--violet)' : 'var(--line)' }}
                  />
                  <span className="text-[11px]" style={{ color: i <= curStep ? 'var(--text-2)' : 'var(--text-3)' }}>{s.label}</span>
                </div>
                {i < STEPS.length - 1 && <span className="h-px w-4" style={{ background: 'var(--line)' }} />}
              </div>
            ))}
          </div>
        )}

        {/* Template downloads */}
        <div className="flex flex-wrap gap-2 mb-6">
          {(['wb','ozon','ym'] as const).map(m => (
            ['finance','products'].map(t => (
              <a
                key={`${m}-${t}`}
                href={`${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/import/templates/${m}/${t}`}
                download
                className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-[var(--r-sm)] border border-[var(--line)] bg-[var(--surface)] text-[var(--text-3)] no-underline transition-colors duration-[var(--dur)] hover:text-[var(--text-2)] hover:border-[var(--violet-text)]"
              >
                <Download size={10} />
                {MP_LABELS[m]} / {TYPE_LABELS[t]}
              </a>
            ))
          ))}
        </div>

        {/* ── STAGE: UPLOAD ── */}
        {stage === 'upload' && (
          <div className="flex flex-col gap-4">
            {/* Drop zone */}
            <div
              onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}
              onClick={() => fileRef.current?.click()}
              className="rounded-[var(--r-md)] px-6 py-10 text-center cursor-pointer transition-[background-color,border-color] duration-150"
              style={{
                border: `2px dashed ${dragging ? 'var(--violet)' : 'var(--line)'}`,
                background: dragging ? 'var(--violet-dim)' : 'var(--surface)',
              }}
            >
              <input ref={fileRef} type="file" accept=".csv" hidden onChange={onFileChange} />
              <div
                className="w-12 h-12 rounded-[var(--r-md)] flex items-center justify-center mx-auto mb-4"
                style={{ background: 'var(--violet-dim)', border: '1px solid var(--violet)' }}
              >
                <Upload size={20} style={{ color: 'var(--violet-text)' }} />
              </div>
              {file ? (
                <div>
                  <p className="text-[15px] font-semibold text-[var(--text)] mb-1 flex items-center justify-center gap-1.5">
                    <FileText size={14} style={{ color: 'var(--violet-text)' }} />
                    {file.name}
                  </p>
                  <p className="text-[12px] text-[var(--text-3)]">{fmtBytes(file.size)}</p>
                </div>
              ) : (
                <div>
                  <p className="text-[15px] font-medium text-[var(--text)] mb-1.5">Перетащите CSV сюда или нажмите для выбора</p>
                  <p className="text-[12px] text-[var(--text-3)]">Только .csv, максимум 10 МБ</p>
                </div>
              )}
            </div>

            {/* Selectors */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={FIELD_LABEL}>Маркетплейс</label>
                <div className="relative">
                  <select value={mp} onChange={e => setMp(e.target.value as MP)} className={SELECT_CLASS}>
                    <option value="">Определить автоматически</option>
                    <option value="wb">Wildberries</option>
                    <option value="ozon">Ozon</option>
                    <option value="ym">Яндекс Маркет</option>
                  </select>
                  <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--text-3)' }} />
                </div>
              </div>
              <div>
                <label className={FIELD_LABEL}>Тип данных</label>
                <div className="relative">
                  <select
                    value={financeFirst ? 'finance' : itype}
                    onChange={e => setIType(e.target.value as IType)}
                    disabled={financeFirst}
                    className={SELECT_CLASS}
                  >
                    {!financeFirst && <option value="">Определить автоматически</option>}
                    <option value="finance">Финансы</option>
                    {!financeFirst && <option value="products">Товары</option>}
                  </select>
                  <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: 'var(--text-3)' }} />
                </div>
              </div>
            </div>

            {financeFirst && (
              <Card variant="surface" className="p-3.5 text-[13px] leading-relaxed text-[var(--text-2)]"
                style={{ borderColor: 'var(--violet)', background: 'var(--violet-dim)' }}>
                <strong className="text-[var(--text)]">Начните с финансового отчёта.</strong>{' '}
                Диагноз PULT строит по вашим финансовым данным — выручке, комиссии, логистике, рекламе.
                Отчёт по товарам можно будет загрузить сразу после него.
              </Card>
            )}

            {error && (
              <div className="text-[13px] rounded-[var(--r-sm)] px-3.5 py-2.5 bg-[var(--danger-dim)] text-[var(--danger)]">
                {error}
              </div>
            )}

            <Button variant="primary" className="w-full" disabled={!file || uploading} loading={uploading} onClick={handleUpload}>
              {uploading ? 'Анализируем файл...' : <><Upload size={15} /> Загрузить и проверить</>}
            </Button>
          </div>
        )}

        {/* ── STAGE: PREVIEW ── */}
        {stage === 'preview' && preview && (
          <div className="flex flex-col gap-4">
            {/* Detection summary */}
            <Card variant="elevated" className="p-5">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  {effectiveMp && <Badge variant="default" className="uppercase">{MP_LABELS[effectiveMp] ?? effectiveMp}</Badge>}
                  {effectiveIType && <Badge variant="neutral">{TYPE_LABELS[effectiveIType] ?? effectiveIType}</Badge>}
                </div>
                <button onClick={reset} className="text-[var(--text-3)] hover:text-[var(--text)] bg-transparent border-0 cursor-pointer transition-colors" aria-label="Отменить">
                  <X size={16} />
                </button>
              </div>

              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: 'СТРОК ВСЕГО', value: preview.total_rows, color: 'var(--text)' },
                  { label: 'КОРРЕКТНЫХ', value: preview.valid_rows, color: 'var(--success)' },
                  { label: 'ПРОПУЩЕНО', value: preview.skipped_rows, color: preview.skipped_rows > 0 ? 'var(--warning)' : 'var(--text)' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="text-center py-3 px-2 rounded-[var(--r-sm)] bg-[var(--surface-h)]">
                    <div className="text-[24px] font-bold [font-variant-numeric:tabular-nums]" style={{ color, fontFamily: 'var(--font-mono)' }}>{value}</div>
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-3)] mt-1">{label}</div>
                  </div>
                ))}
              </div>
            </Card>

            {/* Duplicate warning */}
            {hasDup && !dupAction && (
              <Card variant="surface" className="p-4" style={{ borderColor: 'var(--warning)', background: 'var(--warning-dim)' }}>
                <p className="text-[13px] text-[var(--warning)] mb-3">
                  Этот файл уже импортировался {preview.duplicate_date}. Что делать?
                </p>
                <div className="flex gap-2 flex-wrap">
                  {[
                    { key: 'overwrite', label: 'Перезаписать' },
                    { key: 'new',       label: 'Импортировать как новый' },
                    { key: 'skip',      label: 'Пропустить' },
                  ].map(({ key, label }) => (
                    <Button key={key} variant={key === 'skip' ? 'ghost' : 'secondary'} size="sm"
                      onClick={() => {
                        if (key === 'skip') { reset(); return }
                        setDupAction(key as NonNullable<typeof dupAction>)
                      }}>
                      {label}
                    </Button>
                  ))}
                </div>
              </Card>
            )}

            {/* Blocking errors */}
            {hasErrors && (
              <Card variant="surface" className="p-4" style={{ borderColor: 'var(--danger)', background: 'var(--danger-dim)' }}>
                <p className="text-[13px] font-semibold text-[var(--danger)] mb-2">Блокирующие ошибки:</p>
                {preview.errors.map((e, i) => (
                  <p key={i} className="text-[12px] text-[var(--danger)] mb-1">• {e}</p>
                ))}
              </Card>
            )}

            {/* Warnings (non-blocking) */}
            {preview.warnings.length > 0 && !hasErrors && (
              <Card variant="surface" className="p-3.5" style={{ borderColor: 'var(--warning)', background: 'var(--warning-dim)' }}>
                <p className="text-[12px] font-semibold text-[var(--warning)] mb-1.5 flex items-center gap-1.5">
                  <AlertTriangle size={12} /> Предупреждения ({preview.warnings.length}):
                </p>
                {preview.warnings.slice(0, 5).map((w, i) => (
                  <p key={i} className="text-[11.5px] text-[var(--warning)] opacity-80 mb-0.5">• {w}</p>
                ))}
                {preview.warnings.length > 5 && (
                  <p className="text-[11px] text-[var(--text-3)] mt-1">и ещё {preview.warnings.length - 5} предупреждений...</p>
                )}
              </Card>
            )}

            {/* Column mapping */}
            {Object.keys(preview.mapped_columns).length > 0 && (
              <Card variant="elevated" className="p-4">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-3)] mb-3">Сопоставление колонок</p>
                <div className="flex flex-col gap-1.5">
                  {Object.entries(preview.mapped_columns).map(([field, col]) => (
                    <div key={field} className="flex items-center gap-2 text-[12px]">
                      <span className="text-[var(--text-3)] w-[100px] shrink-0">{field}</span>
                      <ArrowRight size={10} style={{ color: 'var(--text-3)' }} className="shrink-0" />
                      <span style={{ color: col ? 'var(--violet-text)' : 'var(--danger)' }}>{col || '— не найдено'}</span>
                      {col
                        ? <CheckCircle2 size={11} style={{ color: 'var(--success)' }} />
                        : <X size={11} style={{ color: 'var(--danger)' }} />}
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Preview table */}
            {preview.preview_rows.length > 0 && (
              <Card variant="elevated" className="overflow-hidden p-0">
                <div className="px-4 py-3 border-b border-[var(--line)]">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-3)]">Предпросмотр (первые 5 строк)</p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-[12px]">
                    <thead>
                      <tr>
                        {Object.keys(preview.preview_rows[0]).map(k => (
                          <th key={k} className="px-3 py-2 text-left font-medium text-[var(--text-3)] border-b border-[var(--line)] whitespace-nowrap">{k}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.preview_rows.map((row, i) => (
                        <tr key={i} className="border-b border-[var(--line)]">
                          {Object.values(row).map((v, j) => (
                            <td key={j} className="px-3 py-1.5 text-[var(--text)] whitespace-nowrap [font-variant-numeric:tabular-nums]">{String(v ?? '—')}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {/* Action buttons */}
            <div className="flex gap-3">
              <Button variant="ghost" onClick={reset} className="shrink-0">← Назад</Button>
              <Button variant="primary" className="flex-1" disabled={hasErrors || (hasDup && !dupAction)} onClick={handleConfirm}>
                Импортировать {preview.valid_rows} строк <ArrowRight size={14} />
              </Button>
            </div>
          </div>
        )}

        {/* ── STAGE: IMPORTING ── */}
        {stage === 'importing' && (
          <Card variant="surface" className="p-6">
            <p className="text-[16px] font-semibold text-[var(--text)] mb-1">Импортируем данные…</p>
            <p className="text-[13px] text-[var(--text-3)] mb-5">
              {slowImport ? 'Обработка займёт до 90 секунд…' : 'Это займёт несколько секунд'}
            </p>
            {/* Calm skeleton stand-in for the rows being written — no bespoke spinner */}
            <div className="flex flex-col gap-2.5" aria-label="Импорт идёт">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-2/5" />
            </div>
          </Card>
        )}

        {/* ── STAGE: DONE ── */}
        {stage === 'done' && result && (
          <Card variant="surface" className="p-8 text-center">
            <div className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-5"
              style={{ background: 'var(--success-dim)', border: '1px solid var(--success)' }}>
              <CheckCircle2 size={28} style={{ color: 'var(--success)' }} />
            </div>
            <p className="text-[20px] font-bold text-[var(--text)] mb-2">Импорт завершён</p>
            <p className="text-[14px] text-[var(--text-2)] mb-1">
              Импортировано <strong style={{ color: 'var(--success)' }}>{result.imported}</strong> строк
              {result.skipped > 0 && `, пропущено ${result.skipped}`}
            </p>
            <p className="text-[12px] text-[var(--text-3)] mb-8">Диагноз появится на главной в течение минуты</p>

            <div className="flex gap-3 justify-center">
              <Button variant="primary" onClick={() => router.push('/dashboard')}>
                Перейти в Пульт <ArrowRight size={14} />
              </Button>
              <Button variant="ghost" onClick={reset}>
                <RefreshCw size={14} /> Ещё импорт
              </Button>
            </div>
          </Card>
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
    </>
  )
}

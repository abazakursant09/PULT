'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { api, type ImportConfirmResponse, type ImportPreviewResponse, type StoreRef } from '@/lib/api'
import { LedgerShell, LedgerFigure } from '@/components/stores/LedgerShell'
import { CsvDropzone } from '@/components/stores/CsvDropzone'
import { marketplaceLabel } from '@/components/stores/CabinetGroup'
import { IMPORT_TYPE_LABEL } from '@/components/stores/StoreImportsTable'

// The one CSV flow in PULT. It always knows its store — the route carries it, the upload sends
// it, and the backend reads the marketplace from it.
//
// The seller's type choice is a HINT, nothing more: the parser reads the file's own columns and
// answers with the real type. Everything after the upload — the overwrite wording, the confirm —
// is driven by `preview.import_type`, never by what was picked beforehand. When the two differ,
// the screen says so instead of quietly following the file.

type Stage = 'pick' | 'uploading' | 'preview' | 'confirming' | 'done'

const TYPES: { key: string; label: string; hint: string }[] = [
  { key: 'products',     label: 'Товары',                  hint: 'Список товаров магазина: артикул, название, цена, остаток' },
  { key: 'finance',      label: 'Финансы',                 hint: 'Продажи и удержания по дням' },
  { key: 'returns',      label: 'Возвраты',                hint: 'Возвраты по дням' },
  { key: 'card_content', label: 'Данные карточек товаров', hint: 'Заголовки, описания и характеристики карточек' },
]

// Templates that actually exist in the backend (tasks/csv_parser.py). Anything else must not
// show a download button — a 404 behind a promise is worse than no promise.
const TEMPLATES: Record<string, string[]> = {
  wildberries: ['finance', 'products'],
  ozon:        ['finance', 'products'],
  yandex:      ['finance'],
}
const TEMPLATE_CODE: Record<string, string> = { wildberries: 'wb', ozon: 'ozon', yandex: 'ym' }

// The exact scope the backend deletes in overwrite mode (backend/routers/csv_import.py:385-410).
const OVERWRITE_TEXT: Record<string, string> = {
  finance: 'Строки этого магазина за дни, которые есть в файле, будут удалены и записаны заново. Другие дни, другие магазины и данные из API не затрагиваются.',
  returns: 'Возвраты этого магазина за дни, которые есть в файле, будут удалены и записаны заново. Другие дни, другие магазины и данные из API не затрагиваются.',
  products: 'Загруженный файлом список товаров этого магазина будет заменён целиком. Сами товары и их привязка к магазину сохранятся; данные из API не затрагиваются.',
  card_content: 'Данные карточек по артикулам из файла будут заменены во всём кабинете — этот отчёт не делится по магазинам. Артикулы, которых нет в файле, сохранятся; данные из API не затрагиваются.',
}

function typeLabel(t: string | null | undefined): string {
  if (!t) return 'неизвестный тип'
  return IMPORT_TYPE_LABEL[t] ?? t
}

export default function StoreImportPage({ params }: { params: { storeId: string } }) {
  const { storeId } = params

  const [store, setStore]   = useState<StoreRef | null>(null)
  const [head, setHead]     = useState<'loading' | 'ready' | 'blocked'>('loading')
  const [stage, setStage]   = useState<Stage>('pick')
  const [chosenType, setChosenType] = useState('products')
  const [file, setFile]     = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null)
  const [mode, setMode]     = useState<'new' | 'overwrite'>('new')
  const [result, setResult] = useState<ImportConfirmResponse | null>(null)
  const [error, setError]   = useState('')
  // null = still loading. The Business Diagnosis is built from the FINANCIAL report: the Advisory
  // Runtime only considers a seller who has imported finance rows, so a first upload of "Товары"
  // produces no diagnosis. The gate moved here with the flow (it used to live on /dashboard/import)
  // — dropping it would have quietly taken back a guarantee the product already makes.
  const [hasFinance, setHasFinance] = useState<boolean | null>(null)

  useEffect(() => {
    let alive = true
    api.csvImport.history()
      .then(rows => {
        if (alive) setHasFinance(rows.some(r => r.import_type === 'finance' && r.status === 'confirmed'))
      })
      // Fail OPEN: if we cannot read the history we must not lock a seller out of importing.
      .catch(() => { if (alive) setHasFinance(true) })
    return () => { alive = false }
  }, [])

  const financeFirst = hasFinance === false
  useEffect(() => { if (financeFirst) setChosenType('finance') }, [financeFirst])

  const loadHead = useCallback(async () => {
    try {
      const page = await api.marketplaceStores.imports(storeId, { page: 1, page_size: 1 })
      setStore(page.store)
      setHead(page.store.status === 'active' ? 'ready' : 'blocked')
    } catch {
      setHead('blocked')
      setError('Не удалось открыть магазин. Повторите попытку.')
    }
  }, [storeId])

  useEffect(() => { void loadHead() }, [loadHead])

  const upload = async () => {
    if (!file) return
    setStage('uploading'); setError('')
    try {
      const pv = await api.csvImport.upload(file, storeId, chosenType)
      setPreview(pv)
      setStage('preview')
    } catch (e) {
      setStage('pick')
      setError(e instanceof Error && /архивирован/i.test(e.message)
        ? 'Магазин архивирован — импорт недоступен. Восстановите магазин или выберите другой.'
        : 'Не удалось загрузить файл. Проверьте формат и повторите.')
    }
  }

  const confirm = async () => {
    if (!preview) return
    setStage('confirming'); setError('')
    try {
      setResult(await api.csvImport.confirm(preview.import_id, mode))
      setStage('done')
    } catch {
      setStage('preview')
      setError('Импорт не выполнен. Данные магазина не изменились.')
    }
  }

  const restart = () => {
    setFile(null); setPreview(null); setResult(null); setMode('new'); setError(''); setStage('pick')
  }

  const crumbs = [
    { label: 'Магазины', href: '/dashboard/stores' },
    { label: store?.label ?? 'Магазин', href: `/dashboard/stores/${storeId}` },
  ]

  if (head === 'loading') {
    return (
      <LedgerShell crumbs={crumbs} title="Загрузить CSV">
        <p className="l-dim" style={{ padding: '24px 0' }}>Загружаем магазин…</p>
      </LedgerShell>
    )
  }

  if (head === 'blocked') {
    return (
      <LedgerShell crumbs={crumbs} title="Загрузить CSV">
        <hr className="l-rule" />
        <p style={{ padding: '26px 0 0', fontSize: 16, maxWidth: '52ch' }}>
          {error || 'Магазин в архиве. Новые файлы не принимаются, пока магазин не восстановлен.'}
        </p>
        <div style={{ display: 'flex', gap: 12, paddingTop: 18 }}>
          <Link href={`/dashboard/stores/${storeId}`} className="l-btn" style={{ textDecoration: 'none' }}>
            Открыть магазин
          </Link>
          <Link href="/dashboard/import" className="l-btn" style={{ textDecoration: 'none' }}>
            Выбрать другой магазин
          </Link>
        </div>
      </LedgerShell>
    )
  }

  const mp = store?.marketplace ?? ''
  const templateAvailable = (TEMPLATES[mp] ?? []).includes(chosenType)
  const detected = preview?.import_type ?? null
  const mismatch = Boolean(detected && detected !== chosenType)
  const rowsToReplace = preview?.rows_to_replace ?? 0

  return (
    <LedgerShell crumbs={crumbs} title="Загрузить CSV">
      <hr className="l-rule" />
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', borderLeft: '2px solid var(--text)', padding: '2px 0 2px 14px', marginTop: 20, flexWrap: 'wrap' }}>
        <span className="l-caps l-muted">Магазин</span>
        <b className="l-serif" style={{ fontSize: 18, fontWeight: 400 }}>{store?.label}</b>
        <span className="l-dim">· {marketplaceLabel(mp)}</span>
      </div>

      {/* ── Step 1–2: type hint + file ─────────────────────────────────────── */}
      {(stage === 'pick' || stage === 'uploading') && (
        <>
          <h2 className="l-caps l-muted" style={{ padding: '30px 0 12px' }}>Что вы загружаете</h2>
          {financeFirst && (
            <p className="l-dim" style={{ margin: '0 0 12px', maxWidth: '64ch' }}>
              Начните с финансового отчёта: диагноз PULT строится по деньгам. Остальные отчёты
              станут доступны сразу после первого импорта финансов.
            </p>
          )}
          <div style={{ display: 'grid', gap: 0 }}>
            {TYPES.map(t => {
              const locked = financeFirst && t.key !== 'finance'
              return (
                <label
                  key={t.key}
                  style={{
                    display: 'flex', gap: 12, alignItems: 'flex-start', padding: '13px 0',
                    borderBottom: '1px solid var(--line)',
                    cursor: locked ? 'not-allowed' : 'pointer', opacity: locked ? 0.45 : 1,
                  }}
                >
                  <input
                    type="radio"
                    name="import-type"
                    value={t.key}
                    checked={chosenType === t.key}
                    disabled={locked}
                    onChange={() => setChosenType(t.key)}
                    style={{ marginTop: 4, accentColor: 'var(--text)' }}
                  />
                  <span>
                    <span style={{ fontSize: 16 }}>{t.label}</span>
                    <span className="l-dim" style={{ display: 'block', fontSize: 14 }}>{t.hint}</span>
                  </span>
                </label>
              )
            })}
          </div>

          <p className="l-dim" style={{ fontSize: 13.5, paddingTop: 12 }}>
            Это подсказка для вас. Тип отчёта PULT определит по содержимому файла.
          </p>
          <p className="l-dim" style={{ fontSize: 13.5 }}>
            После импорта PULT проанализирует данные и покажет рекомендации на главной.
          </p>

          <div style={{ paddingTop: 10 }}>
            {templateAvailable ? (
              <a
                className="l-link l-caps"
                href={api.csvImport.templateUrl(TEMPLATE_CODE[mp] ?? mp, chosenType)}
              >
                Скачать шаблон
              </a>
            ) : (
              <span className="l-dim" style={{ fontSize: 13.5 }}>Шаблон для этого отчёта пока не готов.</span>
            )}
          </div>

          <CsvDropzone
            file={file}
            onFile={f => { setFile(f); setError('') }}
            onError={setError}
            disabled={stage === 'uploading'}
          />

          {error && <p className="l-oxide" role="alert" style={{ paddingTop: 16 }}>{error}</p>}

          <div style={{ display: 'flex', gap: 12, paddingTop: 24 }}>
            <button type="button" className="l-btn-ink" disabled={!file || stage === 'uploading'} onClick={() => void upload()}>
              {stage === 'uploading' ? 'Проверяем файл…' : 'Проверить файл'}
            </button>
          </div>
        </>
      )}

      {/* ── Step 3: preview ────────────────────────────────────────────────── */}
      {(stage === 'preview' || stage === 'confirming') && preview && (
        <>
          <h2 className="l-serif l-h2" style={{ padding: '30px 0 4px' }}>Проверка файла</h2>
          <p className="l-dim" style={{ margin: '0 0 6px' }}>{file?.name}</p>

          {mismatch && (
            <p
              role="alert"
              className="l-oxide"
              style={{ borderLeft: '2px solid var(--ledger-oxide)', padding: '10px 0 10px 14px', margin: '14px 0 0', maxWidth: '68ch' }}
            >
              Файл распознан как «{typeLabel(detected)}», хотя перед загрузкой вы выбрали
              «{typeLabel(chosenType)}». PULT продолжит работу с типом, определённым по содержимому файла.
            </p>
          )}

          <div style={{ paddingTop: 20 }}>
            <LedgerFigure label="Тип отчёта по содержимому файла" value={typeLabel(detected)} />
            <LedgerFigure label="Строк в файле" value={preview.total_rows} />
            <LedgerFigure label="Будут добавлены" value={preview.new_products ?? 0} />
            <LedgerFigure label="Будут обновлены" value={preview.updates ?? 0} />
            <LedgerFigure
              label="Конфликтуют с уже загруженным"
              value={preview.conflicts ?? 0}
              tone={(preview.conflicts ?? 0) > 0 ? 'oxide' : 'green'}
            />
            <LedgerFigure label="Пропущено строк" value={preview.skipped_rows} />
          </div>

          {preview.errors.length > 0 && (
            <p className="l-oxide" role="alert" style={{ paddingTop: 16 }}>{preview.errors[0]}</p>
          )}

          <h3 className="l-caps l-muted" style={{ padding: '30px 0 10px' }}>Что сделать с данными</h3>
          <label style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '13px 0', borderBottom: '1px solid var(--line)', cursor: 'pointer' }}>
            <input type="radio" name="mode" checked={mode === 'new'} onChange={() => setMode('new')} style={{ marginTop: 4, accentColor: 'var(--text)' }} />
            <span>
              <span style={{ fontSize: 16 }}>Добавить данные</span>
              <span className="l-dim" style={{ display: 'block', fontSize: 14 }}>
                Ничего не удаляется. Строки добавятся к уже загруженным.
              </span>
            </span>
          </label>
          <label style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '13px 0', borderBottom: '1px solid var(--line)', cursor: 'pointer' }}>
            <input type="radio" name="mode" checked={mode === 'overwrite'} onChange={() => setMode('overwrite')} style={{ marginTop: 4, accentColor: 'var(--text)' }} />
            <span>
              <span style={{ fontSize: 16 }}>Заменить данные в области этого отчёта</span>
              <span className="l-dim" style={{ display: 'block', fontSize: 14 }}>
                {OVERWRITE_TEXT[detected ?? ''] ?? 'Будут заменены только строки, загруженные файлом для этого отчёта. Данные из API не затрагиваются.'}
              </span>
              <span className={rowsToReplace > 0 ? 'l-oxide' : 'l-dim'} style={{ display: 'block', fontSize: 14, marginTop: 4 }}>
                {rowsToReplace > 0
                  ? `Будет удалено строк: ${rowsToReplace}`
                  : 'Удалять нечего — в этой области ещё нет загруженных строк.'}
              </span>
            </span>
          </label>

          {error && <p className="l-oxide" role="alert" style={{ paddingTop: 16 }}>{error}</p>}

          <div style={{ display: 'flex', gap: 12, paddingTop: 24, flexWrap: 'wrap' }}>
            <button type="button" className="l-btn-ink" onClick={() => void confirm()} disabled={stage === 'confirming'}>
              {stage === 'confirming' ? 'Импортируем…' : `Импортировать ${preview.total_rows} строк`}
            </button>
            <button type="button" className="l-btn" onClick={restart} disabled={stage === 'confirming'}>Отмена</button>
          </div>
        </>
      )}

      {/* ── Step 4: result ─────────────────────────────────────────────────── */}
      {stage === 'done' && result && (
        <>
          <h2 className="l-serif l-h2" style={{ padding: '30px 0 4px' }}>Импорт завершён</h2>
          <div style={{ paddingTop: 16 }}>
            <LedgerFigure label="Импортировано строк" value={result.imported_count} tone="green" />
            <LedgerFigure label="Пропущено" value={result.skipped_count} />
            {typeof result.replaced === 'number' && <LedgerFigure label="Заменено ранее загруженных" value={result.replaced} />}
            {typeof result.conflicts === 'number' && (
              <LedgerFigure label="Не разобрано строк" value={result.conflicts} tone={result.conflicts > 0 ? 'oxide' : undefined} />
            )}
          </div>

          <p style={{ paddingTop: 16, maxWidth: '64ch' }}>
            Данные получены. PULT анализирует их — рекомендации появятся на главной автоматически.
          </p>
          <p className="l-serif l-dim" style={{ fontStyle: 'italic', paddingTop: 10, fontSize: 15 }}>
            Файл удалён с сервера. PULT хранит только разобранные строки этого магазина.
          </p>

          <div style={{ display: 'flex', gap: 12, paddingTop: 24, flexWrap: 'wrap' }}>
            {(result.conflicts ?? 0) > 0 && preview && (
              <Link href={`/dashboard/imports/${preview.import_id}/conflicts`} className="l-btn-ink" style={{ textDecoration: 'none' }}>
                Разобрать {result.conflicts} строк
              </Link>
            )}
            <button type="button" className="l-btn" onClick={restart}>Загрузить ещё файл</button>
            <Link href={`/dashboard/stores/${storeId}`} className="l-btn" style={{ textDecoration: 'none' }}>
              Вернуться к магазину
            </Link>
          </div>
        </>
      )}
    </LedgerShell>
  )
}

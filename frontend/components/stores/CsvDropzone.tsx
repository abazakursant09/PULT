'use client'

import { useCallback, useRef, useState } from 'react'

// The file step, extracted so there is exactly one of it. Nothing here talks to the network:
// it hands a File back and the import page decides what to do with it.

function fmtBytes(b: number): string {
  if (b < 1024) return `${b} Б`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} КБ`
  return `${(b / 1024 / 1024).toFixed(1)} МБ`
}

export function CsvDropzone({
  file, onFile, onError, disabled,
}: {
  file: File | null
  onFile: (f: File) => void
  onError: (message: string) => void
  disabled?: boolean
}) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const accept = useCallback((f: File | undefined) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.csv')) {
      onError('Поддерживаются только файлы .csv')
      return
    }
    onFile(f)
  }, [onFile, onError])

  return (
    <>
      <div
        onDragOver={e => { e.preventDefault(); if (!disabled) setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); if (!disabled) accept(e.dataTransfer.files[0]) }}
        style={{
          marginTop: 24, padding: '48px 24px', textAlign: 'center',
          border: `1px dashed ${dragging ? 'var(--text)' : 'var(--rule-strong)'}`,
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <h2 className="l-serif" style={{ fontSize: 23, fontWeight: 400, margin: '0 0 8px' }}>
          Перетащите файл отчёта
        </h2>
        <p className="l-dim" style={{ margin: '0 0 20px' }}>По одному файлу за раз. Формат CSV.</p>
        <button type="button" className="l-btn" disabled={disabled} onClick={() => inputRef.current?.click()}>
          Выбрать файл
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          hidden
          aria-label="Файл отчёта"
          onChange={e => accept(e.target.files?.[0])}
        />
      </div>

      {file && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, border: '1px solid var(--rule-strong)', padding: '14px 16px', marginTop: 20 }}>
          <span className="l-wrap" style={{ flex: 1 }}>{file.name}</span>
          <span className="l-num l-dim" style={{ fontSize: 13 }}>{fmtBytes(file.size)}</span>
        </div>
      )}
    </>
  )
}

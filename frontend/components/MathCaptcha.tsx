'use client'
import { useState, useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'

interface Question { text: string; answer: number }

function gen(): Question {
  const a = Math.floor(Math.random() * 12) + 1
  const b = Math.floor(Math.random() * 12) + 1
  if (Math.random() > 0.5) return { text: `${a} + ${b}`, answer: a + b }
  const big = Math.max(a, b), small = Math.min(a, b)
  return { text: `${big} − ${small}`, answer: big - small }
}

interface Props {
  onValid: (ok: boolean) => void
}

export function MathCaptcha({ onValid }: Props) {
  const [q, setQ] = useState<Question>(gen)
  const [val, setVal] = useState('')

  const refresh = useCallback(() => {
    setQ(gen())
    setVal('')
    onValid(false)
  }, [onValid])

  useEffect(() => {
    const n = parseInt(val, 10)
    onValid(!isNaN(n) && n === q.answer && val.trim() !== '')
  }, [val, q, onValid])

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ flex: 1 }}>
        <label className="label mb-2" style={{ display: 'block' }}>
          Антибот: сколько будет&nbsp;
          <span style={{ color: '#2563EB', fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 700 }}>
            {q.text} =&nbsp;?
          </span>
        </label>
        <input
          type="number"
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder="Ответ..."
          className="input"
          style={{ MozAppearance: 'textfield' }}
          required
        />
      </div>
      <button
        type="button"
        onClick={refresh}
        title="Новый пример"
        style={{
          marginTop: 22,
          padding: '10px 12px',
          borderRadius: 10,
          border: '1.5px solid rgba(0,0,0,0.1)',
          background: 'transparent',
          cursor: 'pointer',
          color: '#9A9897',
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <RefreshCw size={15} />
      </button>
    </div>
  )
}

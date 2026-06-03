'use client'

import { useState } from 'react'
import { Trophy, X, Send, Check } from 'lucide-react'
import { api } from '@/lib/api'

const A   = '#3B82F6'
const ABG = 'rgba(26,115,232,0.08)'
const ABR = 'rgba(26,115,232,0.22)'

interface Props {
  autoTitle: string
  onClose: () => void
}

export function ShareSuccessModal({ autoTitle, onClose }: Props) {
  const [title,      setTitle]      = useState(autoTitle)
  const [text,       setText]       = useState('')
  const [authorName, setAuthorName] = useState('')
  const [published,  setPublished]  = useState(false)
  const [loading,    setLoading]    = useState(false)

  async function publish() {
    if (!text.trim()) return
    setLoading(true)

    const story = {
      id:          Date.now().toString(),
      title:       title.trim() || autoTitle,
      text:        text.trim(),
      author_name: authorName.trim() || null,
      date:        new Date().toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }),
    }

    // Save to localStorage as local backup
    try {
      const raw = localStorage.getItem('bp_success_stories')
      const arr = raw ? JSON.parse(raw) : []
      arr.unshift(story)
      localStorage.setItem('bp_success_stories', JSON.stringify(arr.slice(0, 20)))
    } catch {}

    // Post to backend if authenticated
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null
      if (token) {
        await api.successStories.create({
          title:       story.title,
          text:        story.text,
          author_name: authorName.trim() || undefined,
        })
      }
    } catch {}

    setLoading(false)
    setPublished(true)
    setTimeout(onClose, 1800)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,0.3)',  }} />
      <div
        className="card p-6 relative w-full max-w-md animate-slide-up"
        onClick={e => e.stopPropagation()}
      >
        <div className="card-line" />

        <div className="flex items-start justify-between gap-3 mb-5">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: ABG, border: `1px solid ${ABR}` }}
            >
              <Trophy size={18} style={{ color: A }} />
            </div>
            <div>
              <h2 className="font-bold text-base leading-none" style={{ color: '#202124' }}>Поделиться успехом</h2>
              <p className="text-[11px] mt-1" style={{ color: 'rgba(0,0,0,0.38)' }}>История будет опубликована в Обзоре рынка</p>
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost shrink-0" style={{ padding: 6, width: 30, height: 30 }}>
            <X size={13} />
          </button>
        </div>

        {published ? (
          <div
            className="flex items-center justify-center gap-2 py-8 rounded-2xl"
            style={{ background: ABG, border: `1px solid ${ABR}` }}
          >
            <div className="w-8 h-8 rounded-full flex items-center justify-center" style={{ background: A }}>
              <Check size={15} style={{ color: '#fff' }} />
            </div>
            <span className="font-semibold text-sm" style={{ color: A }}>История опубликована!</span>
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="label mb-2">Заголовок</label>
              <input
                className="input"
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Мой результат..."
              />
            </div>
            <div>
              <label className="label mb-2">Расскажите подробнее</label>
              <textarea
                rows={4}
                className="input w-full resize-none"
                placeholder="Что помогло, какие шаги сделали результат возможным..."
                value={text}
                onChange={e => setText(e.target.value)}
                style={{ fontFamily: 'inherit', fontSize: '0.9rem' }}
              />
            </div>
            <div>
              <label className="label mb-2">Ваше имя <span style={{ color: 'rgba(0,0,0,0.38)', fontWeight: 400 }}>(необязательно)</span></label>
              <input
                className="input"
                value={authorName}
                onChange={e => setAuthorName(e.target.value)}
                placeholder="Продавец Иван или оставьте пустым"
              />
            </div>
            <button
              onClick={publish}
              disabled={!text.trim() || loading}
              className="btn btn-primary w-full"
              style={{ gap: 8 }}
            >
              {loading
                ? <><span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> Публикуем...</>
                : <><Send size={14} /> Опубликовать</>
              }
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

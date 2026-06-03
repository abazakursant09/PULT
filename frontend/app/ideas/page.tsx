'use client'

import { useState, useEffect } from 'react'
import { AppShell } from '@/components/AppShell'
import { Lightbulb, ThumbsUp, Clock, Zap, CheckCircle2, Plus, Send, X, Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface Idea {
  id: string
  author_name: string
  topic: string
  description?: string
  importance: string
  status: string
  votes: number
  created_at: string
}

const IMPORTANCE_META: Record<string, { label: string; color: string; bg: string; border: string }> = {
  'критично': { label: 'Критично',  color: '#C62828', bg: 'rgba(217,48,37,0.08)',   border: 'rgba(217,48,37,0.25)' },
  'важно':    { label: 'Важно',     color: '#E65100', bg: 'rgba(242,153,0,0.09)',   border: 'rgba(242,153,0,0.28)' },
  'хотелка':  { label: 'Хотелка',  color: '#1A73E8', bg: 'rgba(26,115,232,0.08)',  border: 'rgba(26,115,232,0.22)' },
}

const STATUS_META: Record<string, { label: string; icon: typeof Clock; color: string; bg: string; border: string }> = {
  'на рассмотрении': { label: 'На рассмотрении', icon: Clock,        color: '#5F6368', bg: 'rgba(0,0,0,0.05)',         border: 'rgba(0,0,0,0.10)'         },
  'в работе':        { label: 'В работе',         icon: Zap,          color: '#1A73E8', bg: 'rgba(26,115,232,0.08)',   border: 'rgba(26,115,232,0.22)'    },
  'сделано':         { label: 'Сделано',          icon: CheckCircle2, color: '#1A6B37', bg: 'rgba(21,128,61,0.08)',    border: 'rgba(21,128,61,0.22)'     },
}

export default function IdeasPage() {
  const [ideas,   setIdeas]   = useState<Idea[]>([])
  const [loading, setLoading] = useState(true)
  const [show,    setShow]    = useState(false)
  const [voted,   setVoted]   = useState<Set<string>>(new Set())
  const [filter,  setFilter]  = useState<string>('all')

  const [form, setForm] = useState({ topic: '', description: '', importance: 'хотелка', author_name: '' })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const v = localStorage.getItem('bp_voted_ideas')
    if (v) try { setVoted(new Set(JSON.parse(v))) } catch {}
    fetchIdeas()
  }, [])

  async function fetchIdeas() {
    try {
      const r = await fetch(`${API}/api/ideas`)
      if (r.ok) setIdeas(await r.json())
    } finally { setLoading(false) }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.topic.trim()) { setError('Введите тему'); return }
    setSubmitting(true); setError('')
    try {
      const r = await fetch(`${API}/api/ideas`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, author_name: form.author_name || 'Аноним' }),
      })
      if (!r.ok) throw new Error(await r.text())
      const idea: Idea = await r.json()
      setIdeas(prev => [idea, ...prev])
      setForm({ topic: '', description: '', importance: 'хотелка', author_name: '' })
      setShow(false)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Ошибка')
    } finally { setSubmitting(false) }
  }

  async function vote(id: string) {
    if (voted.has(id)) return
    try {
      const r = await fetch(`${API}/api/ideas/${id}/vote`, { method: 'POST' })
      if (r.ok) {
        const updated: Idea = await r.json()
        setIdeas(prev => prev.map(i => i.id === id ? updated : i))
        const next = new Set(voted).add(id)
        setVoted(next)
        localStorage.setItem('bp_voted_ideas', JSON.stringify(Array.from(next)))
      }
    } catch {}
  }

  const statuses = ['на рассмотрении', 'в работе', 'сделано'] as const
  const filtered = filter === 'all' ? ideas : ideas.filter(i => i.status === filter)

  const counts = {
    'на рассмотрении': ideas.filter(i => i.status === 'на рассмотрении').length,
    'в работе':        ideas.filter(i => i.status === 'в работе').length,
    'сделано':         ideas.filter(i => i.status === 'сделано').length,
  }

  return (
    <AppShell>
      <div className="p-5 sm:p-8 max-w-4xl mx-auto">

        {/* Header */}
        <div className="flex items-start justify-between gap-4 mb-8">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'rgba(26,115,232,0.10)', border: '1px solid rgba(26,115,232,0.20)' }}>
                <Lightbulb size={20} style={{ color: '#1A73E8' }} />
              </div>
              <h1 className="font-bold" style={{ fontSize: '1.5rem', color: '#202124' }}>Пульт будущего</h1>
            </div>
            <p style={{ color: '#5F6368', fontSize: '0.9375rem' }}>
              Предложите функцию или улучшение — голосуйте за идеи сообщества
            </p>
          </div>

          <Button onClick={() => setShow(true)} style={{ whiteSpace: 'nowrap' }}>
            <Plus size={16} /> Предложить идею
          </Button>
        </div>

        {/* Status roadmap tabs */}
        <div className="flex items-center gap-2 flex-wrap mb-6">
          <button
            onClick={() => setFilter('all')}
            className="px-4 py-2 rounded-full text-sm font-medium transition-colors"
            style={{ background: filter === 'all' ? '#1A73E8' : 'rgba(0,0,0,0.05)', color: filter === 'all' ? '#FFFFFF' : '#5F6368', border: filter === 'all' ? 'none' : '1px solid rgba(0,0,0,0.10)' }}
          >
            Все · {ideas.length}
          </button>
          {statuses.map(s => {
            const meta = STATUS_META[s]
            const Icon = meta.icon
            return (
              <button
                key={s}
                onClick={() => setFilter(s)}
                className="flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-medium transition-colors"
                style={{ background: filter === s ? meta.bg : 'rgba(0,0,0,0.04)', color: filter === s ? meta.color : '#5F6368', border: filter === s ? `1px solid ${meta.border}` : '1px solid rgba(0,0,0,0.08)' }}
              >
                <Icon size={13} />
                {meta.label} · {counts[s]}
              </button>
            )
          })}
        </div>

        {/* Ideas list */}
        {loading ? (
          <div className="flex justify-center py-16">
            <Loader2 size={28} className="animate-spin text-muted-foreground" />
          </div>
        ) : filtered.length === 0 ? (
          <Card className="p-16 text-center">
            <Lightbulb size={32} className="mx-auto mb-4" style={{ color: '#1A73E8', opacity: 0.4 }} />
            <p style={{ color: '#5F6368' }}>Идей пока нет — будьте первым!</p>
          </Card>
        ) : (
          <div className="flex flex-col gap-4">
            {filtered.map(idea => {
              const imp  = IMPORTANCE_META[idea.importance] ?? IMPORTANCE_META['хотелка']
              const stat = STATUS_META[idea.status] ?? STATUS_META['на рассмотрении']
              const StatIcon = stat.icon
              return (
                <Card key={idea.id} className="p-6 flex items-start gap-5">
                  {/* Vote */}
                  <button
                    onClick={() => vote(idea.id)}
                    disabled={voted.has(idea.id)}
                    className="flex flex-col items-center gap-1 shrink-0 rounded-xl px-3 py-2 transition-colors"
                    style={{ background: voted.has(idea.id) ? 'rgba(26,115,232,0.08)' : 'rgba(0,0,0,0.04)', border: voted.has(idea.id) ? '1px solid rgba(26,115,232,0.22)' : '1px solid rgba(0,0,0,0.08)', cursor: voted.has(idea.id) ? 'default' : 'pointer', minWidth: 52 }}
                  >
                    <ThumbsUp size={16} style={{ color: voted.has(idea.id) ? '#1A73E8' : '#5F6368' }} />
                    <span className="font-bold text-sm" style={{ color: voted.has(idea.id) ? '#1A73E8' : '#202124' }}>{idea.votes}</span>
                  </button>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-2">
                      <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold" style={{ background: imp.bg, color: imp.color, border: `1px solid ${imp.border}` }}>
                        {imp.label}
                      </span>
                      <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold" style={{ background: stat.bg, color: stat.color, border: `1px solid ${stat.border}` }}>
                        <StatIcon size={11} />
                        {stat.label}
                      </span>
                    </div>

                    <h3 className="font-semibold mb-1" style={{ color: '#202124', fontSize: '1rem' }}>{idea.topic}</h3>
                    {idea.description && (
                      <p className="text-sm leading-relaxed mb-2" style={{ color: '#5F6368' }}>{idea.description}</p>
                    )}
                    <p className="text-xs" style={{ color: '#5F6368' }}>
                      {idea.author_name} · {new Date(idea.created_at).toLocaleDateString('ru-RU')}
                    </p>
                  </div>
                </Card>
              )
            })}
          </div>
        )}
      </div>

      {/* Modal: add idea */}
      {show && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.25)' }}>
          <div className="w-full max-w-lg rounded-2xl p-8" style={{ background: '#FFFFFF', boxShadow: '0 16px 48px rgba(0,0,0,0.16)' }}>
            <div className="flex items-center justify-between mb-6">
              <h2 className="font-bold text-lg" style={{ color: '#202124' }}>Предложить идею</h2>
              <button onClick={() => setShow(false)} className="p-1.5 rounded-lg transition-colors" style={{ color: '#5F6368' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#F1F3F4' }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '' }}>
                <X size={18} />
              </button>
            </div>

            {error && (
              <div className="mb-4 px-4 py-3 rounded-xl text-sm" style={{ background: 'rgba(217,48,37,0.07)', border: '1px solid rgba(217,48,37,0.22)', color: '#C62828' }}>{error}</div>
            )}

            <form onSubmit={submit} className="space-y-4">
              <div>
                <Label className="mb-2">Ваше имя (необязательно)</Label>
                <Input type="text" placeholder="Аноним" value={form.author_name} onChange={e => setForm(f => ({ ...f, author_name: e.target.value }))} maxLength={80} />
              </div>

              <div>
                <Label className="mb-2">Тема <span style={{ color: '#D93025' }}>*</span></Label>
                <Input type="text" placeholder="Краткое название функции или улучшения" value={form.topic} onChange={e => setForm(f => ({ ...f, topic: e.target.value }))} maxLength={200} required />
              </div>

              <div>
                <Label className="mb-2">Описание</Label>
                <Textarea placeholder="Расскажите подробнее: зачем это нужно, как должно работать..." value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} maxLength={2000} rows={4} style={{ resize: 'vertical', lineHeight: 1.6 }} />
              </div>

              <div>
                <Label className="mb-2">Важность</Label>
                <div className="flex gap-2">
                  {(['хотелка', 'важно', 'критично'] as const).map(v => {
                    const meta = IMPORTANCE_META[v]
                    return (
                      <button key={v} type="button" onClick={() => setForm(f => ({ ...f, importance: v }))}
                        className="flex-1 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors"
                        style={{ background: form.importance === v ? meta.bg : 'rgba(0,0,0,0.04)', color: form.importance === v ? meta.color : '#5F6368', border: form.importance === v ? `1px solid ${meta.border}` : '1px solid rgba(0,0,0,0.08)' }}>
                        {meta.label}
                      </button>
                    )
                  })}
                </div>
              </div>

              <div className="flex gap-3 pt-2">
                <Button type="button" variant="ghost" className="flex-1" onClick={() => setShow(false)}>Отмена</Button>
                <Button type="submit" loading={submitting} className="flex-1">
                  {!submitting && <><Send size={14} /> Отправить</>}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </AppShell>
  )
}

'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Send, Lock, ArrowUpRight, Users, RefreshCw } from 'lucide-react'
import { api, type ChatMessage, type User } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const PLAN_LABELS: Record<string, string> = {
  master:  'Мастер',
  profi:   'Профи',
  maximum: 'Максимальный',
}

function formatTime(iso: string) {
  const d = new Date(iso)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

function Avatar({ name, size = 28 }: { name: string; size?: number }) {
  return (
    <div
      className="rounded-full flex items-center justify-center shrink-0 font-bold"
      style={{
        width: size, height: size,
        background: 'rgba(26,115,232,0.15)',
        color: '#1A73E8',
        fontSize: size * 0.4,
      }}
    >
      {name.charAt(0).toUpperCase()}
    </div>
  )
}

// ── Locked state for Master plan ───────────────────────────────────────────────
function LockedView({ plan }: { plan: string }) {
  const router = useRouter()
  return (
    <div className="flex-1 flex items-center justify-center p-6">
      <Card className="shadow-stripe-lg max-w-sm w-full">
        <CardContent className="p-10 text-center">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center mx-auto mb-6" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.18)' }}>
            <Lock size={28} style={{ color: '#1A73E8' }} />
          </div>
          <h2 className="font-bold text-xl mb-2" style={{ color: '#0A2540' }}>Биржа закрыта</h2>
          <p className="text-muted-foreground mb-2 leading-relaxed">
            Закрытый чат для селлеров доступен на тарифах&nbsp;
            <strong style={{ color: '#1A73E8' }}>Профи</strong> и&nbsp;
            <strong style={{ color: '#1A73E8' }}>Максимальный</strong>.
          </p>
          <p className="text-sm text-muted-foreground mb-8">Ваш тариф: {PLAN_LABELS[plan] ?? plan}</p>
          <Button className="w-full gap-2" onClick={() => router.push('/dashboard')}>
            Сменить тариф <ArrowUpRight size={15} />
          </Button>
          <p className="mt-4 text-xs text-muted-foreground">Обновите тариф в настройках аккаунта</p>
        </CardContent>
      </Card>
    </div>
  )
}

// ── Main chat ─────────────────────────────────────────────────────────────────
export default function ChatPage() {
  const router = useRouter()
  const [user, setUser]         = useState<User | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [fetching, setFetching] = useState(false)
  const [warning, setWarning]   = useState<string | null>(null)
  const [error, setError]       = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  // Load user from localStorage
  useEffect(() => {
    const s = localStorage.getItem('user')
    if (!s) { router.push('/login'); return }
    setUser(JSON.parse(s))
  }, [router])

  const loadMessages = useCallback(async () => {
    setFetching(true)
    try {
      const data = await api.chat.messages()
      setMessages(data)
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes('chat_access_denied')) return
      setError('Не удалось загрузить сообщения')
    } finally {
      setFetching(false)
    }
  }, [])

  useEffect(() => {
    if (user && (user.plan === 'profi' || user.plan === 'maximum') && !user.chat_blocked) {
      loadMessages()
      const id = setInterval(loadMessages, 8000)
      return () => clearInterval(id)
    }
  }, [user, loadMessages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send() {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setWarning(null)
    setLoading(true)
    try {
      const res = await api.chat.send(text)
      if (res.ok && res.message) {
        setMessages(ms => [...ms, res.message!])
      } else if (res.warning) {
        setWarning(res.warning)
        // Refresh user to update chat_blocked status
        const updated = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/auth/me`, {
          headers: { Authorization: `Bearer ${localStorage.getItem('token')}` }
        }).then(r => r.ok ? r.json() : null).catch(() => null)
        if (updated) {
          localStorage.setItem('user', JSON.stringify(updated))
          setUser(updated)
        }
      }
    } catch (e: unknown) {
      setWarning(e instanceof Error ? e.message : 'Ошибка отправки')
    } finally {
      setLoading(false)
    }
  }

  if (!user) return null

  const canAccess = (user.plan === 'profi' || user.plan === 'maximum') && !user.chat_blocked

  if (!canAccess) {
    return (
      <>
        <div className="flex-1 flex flex-col" style={{ background: '#F6F9FC' }}>
          <div className="px-6 py-4 bg-white border-b border-border/60 flex items-center gap-3 shadow-sm">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.15)' }}>
              <Users size={17} style={{ color: '#1A73E8' }} />
            </div>
            <div>
              <h1 className="font-semibold text-lg" style={{ color: '#0A2540' }}>Биржа</h1>
              <p className="text-xs text-muted-foreground">Закрытый чат селлеров</p>
            </div>
          </div>
          <LockedView plan={user.plan ?? 'master'} />
        </div>
      </>
    )
  }

  // Group messages by date
  const grouped: { date: string; msgs: ChatMessage[] }[] = []
  for (const m of messages) {
    const d = formatDate(m.created_at)
    const last = grouped[grouped.length - 1]
    if (last && last.date === d) last.msgs.push(m)
    else grouped.push({ date: d, msgs: [m] })
  }

  return (
    <>
      <div className="flex-1 flex flex-col overflow-hidden" style={{ background: '#F6F9FC' }}>

        {/* Header */}
        <div className="px-6 py-4 flex items-center justify-between shrink-0 bg-white border-b border-border/60 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.15)' }}>
              <Users size={17} style={{ color: '#1A73E8' }} />
            </div>
            <div>
              <h1 className="font-semibold text-lg" style={{ color: '#0A2540' }}>Биржа</h1>
              <p className="text-xs text-muted-foreground">Закрытый чат селлеров · {PLAN_LABELS[user.plan]}</p>
            </div>
          </div>
          <button
            onClick={loadMessages}
            disabled={fetching}
            className="w-8 h-8 flex items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted transition-colors"
            title="Обновить"
          >
            <RefreshCw size={14} className={fetching ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4">
          {error && <p className="text-center text-sm py-4 text-muted-foreground">{error}</p>}
          {messages.length === 0 && !fetching && (
            <div className="flex flex-col items-center justify-center h-full gap-3 py-16">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.15)' }}>
                <Users size={24} style={{ color: '#1A73E8' }} />
              </div>
              <p className="text-muted-foreground">Будьте первым — начните разговор</p>
            </div>
          )}

          {grouped.map(({ date, msgs }) => (
            <div key={date}>
              <div className="flex items-center gap-3 my-4">
                <div className="flex-1 h-px bg-border/60" />
                <span className="text-xs text-muted-foreground px-2">{date}</span>
                <div className="flex-1 h-px bg-border/60" />
              </div>
              {msgs.map((msg, i) => {
                const isMe = msg.user_id === user.id
                const prevMsg = i > 0 ? msgs[i - 1] : null
                const sameSender = prevMsg?.user_id === msg.user_id
                return (
                  <div key={msg.id} className={`flex gap-2.5 ${isMe ? 'flex-row-reverse' : 'flex-row'} ${sameSender ? 'mt-0.5' : 'mt-3'}`}>
                    {!sameSender
                      ? <Avatar name={msg.user_name} size={30} />
                      : <div style={{ width: 30 }} />
                    }
                    <div style={{ maxWidth: '70%' }}>
                      {!sameSender && (
                        <p className={`text-xs mb-1 font-medium text-muted-foreground ${isMe ? 'text-right' : ''}`}>
                          {isMe ? 'Вы' : msg.user_name}
                        </p>
                      )}
                      <div
                        className="text-sm leading-relaxed px-3.5 py-2.5"
                        style={isMe
                          ? { background: '#1A73E8', color: 'white', borderRadius: '16px 4px 16px 16px', boxShadow: '0 2px 8px rgba(26,115,232,0.28)' }
                          : { background: 'white', border: '1px solid hsl(var(--border))', color: '#0A2540', borderRadius: '4px 16px 16px 16px', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }
                        }
                      >
                        {msg.message}
                      </div>
                      <p className={`text-[10px] mt-1 text-muted-foreground ${isMe ? 'text-right' : ''}`}>
                        {formatTime(msg.created_at)}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Warning banner */}
        {warning && (
          <div className="mx-4 sm:mx-6 mb-2 px-4 py-3 rounded-xl text-sm bg-red-50 border border-red-200 text-red-700">
            {warning}
            {user.chat_blocked && <span className="block mt-1 font-medium">Ваш аккаунт заблокирован в Бирже.</span>}
          </div>
        )}

        {/* Blocked notice */}
        {user.chat_blocked && (
          <div className="mx-4 sm:mx-6 mb-3 px-4 py-3 rounded-xl text-sm text-center bg-muted border border-border/60 text-muted-foreground">
            Доступ к чату заблокирован за нарушения правил
          </div>
        )}

        {/* Input */}
        {!user.chat_blocked && (
          <div className="px-4 sm:px-6 py-3 shrink-0 bg-white border-t border-border/60">
            <div className="flex gap-2 max-w-3xl mx-auto">
              <Input
                type="text"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                placeholder="Написать сообщение..."
                disabled={loading}
                maxLength={1000}
                className="flex-1"
              />
              <Button
                onClick={send}
                disabled={loading || !input.trim()}
                size="icon"
                className="shrink-0 w-11 h-10"
                aria-label="Отправить"
              >
                <Send size={15} />
              </Button>
            </div>
            {user.chat_violations > 0 && (
              <p className="text-center mt-1.5 text-xs text-muted-foreground">
                Нарушений: {user.chat_violations}/4
                {user.chat_violations >= 2 && ` · +${(user.chat_violations - 1) * 5}% к следующей подписке`}
              </p>
            )}
          </div>
        )}
      </div>
    </>
  )
}

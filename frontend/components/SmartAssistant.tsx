'use client'

import { useState, useRef, useEffect } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { MessageCircle, X, Send, Sparkles } from 'lucide-react'
import { api, type AssistantResponse } from '@/lib/api'

interface Message {
  role: 'user' | 'assistant'
  text: string
  action?: string | null
  action_label?: string | null
}

const WELCOME: Message = {
  role: 'assistant',
  text: 'Привет! Я Пульт-ассистент. Спросите про конкурентов, отзывы, ценообразование, финансы или любой другой модуль.',
}

const TAB_ACTIONS: Record<string, string> = {
  show_competitors: 'competitors',
  show_reviews:     'reviews',
  show_pricing:     'pricing',
  show_finance:     'finance',
  show_legal:       'legal',
}

export function SmartAssistant() {
  const router   = useRouter()
  const pathname = usePathname()
  const [open,     setOpen]     = useState(false)
  const [messages, setMessages] = useState<Message[]>([WELCOME])
  const [input,    setInput]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const productId = pathname?.match(/^\/dashboard\/products\/([^/]+)/)?.[1] ?? null

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, open])

  function handleAction(action: string | null | undefined) {
    if (!action) return
    if (TAB_ACTIONS[action]) {
      window.dispatchEvent(new CustomEvent('assistant-tab', { detail: { tab: TAB_ACTIONS[action] } }))
    } else if (action === 'go_monitor') {
      router.push('/dashboard/monitor')
    } else if (action === 'go_dashboard') {
      router.push('/dashboard')
    } else if (action === 'go_startup') {
      router.push('/startup')
    }
  }

  async function send() {
    const q = input.trim()
    if (!q || loading) return
    setInput('')
    setMessages(ms => [...ms, { role: 'user', text: q }])
    setLoading(true)
    try {
      const data: AssistantResponse = await api.assistant.ask(q, productId)
      setMessages(ms => [...ms, {
        role:         'assistant',
        text:         data.message,
        action:       data.action,
        action_label: data.action_label,
      }])
    } catch {
      setMessages(ms => [...ms, { role: 'assistant', text: 'Что-то пошло не так. Попробуйте ещё раз.' }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div data-smart-assistant>
      {/* ── Chat window ── */}
      {open && (
        <div
          className="fixed z-50 flex flex-col"
          style={{
            bottom: 88,
            right: 24,
            width: 'min(380px, calc(100vw - 32px))',
            maxHeight: 'calc(100vh - 120px)',
            height: 520,
            background: 'rgba(18,18,22,0.98)',
            border: '1px solid rgba(26,115,232,0.2)',
            borderRadius: 20,
            boxShadow: '0 24px 64px rgba(0,0,0,0.7), 0 0 40px rgba(26,115,232,0.06)',
          }}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-5 py-4 shrink-0 rounded-t-[20px]"
            style={{ background: '#F1F3F4', borderBottom: '1px solid rgba(26,115,232,0.12)' }}
          >
            <div className="flex items-center gap-2.5">
              <div
                className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
                style={{
                  background: 'linear-gradient(135deg, #3B82F6 0%, #2563EB 100%)',
                  boxShadow: '0 2px 8px rgba(26,115,232,0.3)',
                }}
              >
                <Sparkles size={14} style={{ color: '#F8F9FA' }} />
              </div>
              <div>
                <p className="text-sm font-semibold leading-none" style={{ color: '#202124' }}>Пульт-ассистент</p>
                <p className="text-[10px] mt-0.5 font-medium" style={{ color: 'rgba(0,0,0,0.38)' }}>ИИ-помощник платформы</p>
              </div>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="w-7 h-7 flex items-center justify-center rounded-lg transition-colors"
              style={{ color: 'rgba(0,0,0,0.38)', border: '1px solid rgba(26,115,232,0.15)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(26,115,232,0.08)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
              aria-label="Закрыть"
            >
              <X size={14} />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3" style={{ background: 'rgba(12,12,16,0.6)' }}>
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div style={{ maxWidth: '85%' }}>
                  <div
                    className="text-sm leading-relaxed px-3.5 py-2.5"
                    style={msg.role === 'user'
                      ? {
                          background: 'linear-gradient(135deg, #3B82F6 0%, #2563EB 100%)',
                          color: '#F8F9FA',
                          fontWeight: 500,
                          borderRadius: '16px 16px 4px 16px',
                          boxShadow: '0 2px 8px rgba(26,115,232,0.25)',
                        }
                      : {
                          background: '#F1F3F4',
                          border: '1px solid rgba(26,115,232,0.12)',
                          color: '#202124',
                          borderRadius: '16px 16px 16px 4px',
                          boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
                        }
                    }
                  >
                    {msg.text}
                  </div>

                  {msg.action && msg.action_label && (
                    <button
                      onClick={() => handleAction(msg.action)}
                      className="mt-2 text-xs px-3 py-1.5 rounded-xl font-semibold transition-all duration-150 hover:opacity-80"
                      style={{
                        background: 'rgba(26,115,232,0.08)',
                        border: '1px solid rgba(26,115,232,0.28)',
                        color: '#1A73E8',
                        display: 'block',
                      }}
                    >
                      {msg.action_label} →
                    </button>
                  )}
                </div>
              </div>
            ))}

            {/* Typing indicator */}
            {loading && (
              <div className="flex justify-start">
                <div
                  className="px-3.5 py-3 rounded-2xl"
                  style={{
                    background: '#F1F3F4',
                    border: '1px solid rgba(26,115,232,0.12)',
                  }}
                >
                  <div className="flex gap-1 items-center h-3">
                    {[0, 1, 2].map(j => (
                      <div
                        key={j}
                        className="w-1.5 h-1.5 rounded-full"
                        style={{
                          background: '#3B82F6',
                          animation: `dotAppear 0.3s ease-out ${j * 0.1}s both`,
                        }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div
            className="px-4 py-3 shrink-0 rounded-b-[20px]"
            style={{ borderTop: '1px solid rgba(26,115,232,0.1)', background: 'rgba(16,16,20,0.9)' }}
          >
            <div className="flex gap-2">
              <input
                type="text"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                placeholder="Задайте вопрос..."
                className="flex-1 rounded-xl text-sm outline-none"
                style={{
                  height: 40,
                  padding: '0 14px',
                  background: '#F1F3F4',
                  border: '1px solid rgba(26,115,232,0.18)',
                  color: '#202124',
                  fontFamily: 'inherit',
                }}
                disabled={loading}
              />
              <button
                onClick={send}
                disabled={loading || !input.trim()}
                className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-all duration-150"
                style={{
                  background: input.trim() && !loading
                    ? 'linear-gradient(135deg, #3B82F6 0%, #2563EB 100%)'
                    : 'rgba(0,0,0,0.04)',
                  border: `1px solid ${input.trim() && !loading ? 'rgba(26,115,232,0.5)' : 'rgba(0,0,0,0.08)'}`,
                  color: input.trim() && !loading ? '#F8F9FA' : 'rgba(255,255,255,0.3)',
                  boxShadow: input.trim() && !loading ? '0 2px 8px rgba(26,115,232,0.28)' : 'none',
                }}
                aria-label="Отправить"
              >
                <Send size={15} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Floating trigger ── */}
      <button
        onClick={() => setOpen(v => !v)}
        className="fixed z-50 w-14 h-14 rounded-full flex items-center justify-center transition-all duration-200"
        style={{
          bottom: 24,
          right: 24,
          background: open
            ? '#F1F3F4'
            : 'linear-gradient(135deg, #3B82F6 0%, #2563EB 100%)',
          border: `1px solid ${open ? 'rgba(26,115,232,0.3)' : 'rgba(26,115,232,0.5)'}`,
          boxShadow: open
            ? '0 4px 16px rgba(0,0,0,0.4)'
            : '0 0 24px rgba(26,115,232,0.3), 0 8px 28px rgba(0,0,0,0.4)',
          color: open ? '#3B82F6' : '#F8F9FA',
        }}
        aria-label={open ? 'Закрыть ассистента' : 'Открыть ассистента'}
      >
        {open ? <X size={20} /> : <MessageCircle size={22} />}
      </button>
    </div>
  )
}

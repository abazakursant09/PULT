'use client'

import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Send, Bot, TrendingUp, Star, Scale, Package, BarChart2, Zap, ArrowLeft } from 'lucide-react'

interface Agent {
  id:       string
  name:     string
  desc:     string
  icon:     React.ElementType
  prompt:   string
}

interface Message {
  role: 'user' | 'agent'
  text: string
  ts:   string
}

const AGENTS: Agent[] = [
  { id: 'pricing',     name: 'Ценообразование', icon: TrendingUp, desc: 'Демо-сценарий обсуждения цены',       prompt: 'Демо: здесь можно посмотреть пример диалога о цене. Данные товаров и конкурентов не загружаются.' },
  { id: 'reviews',     name: 'Отзывы',          icon: Star,       desc: 'Демо-сценарий работы с отзывом',      prompt: 'Демо: пример диалога о черновике ответа. Отзывы покупателей не загружаются, ответы не отправляются.' },
  { id: 'legal',       name: 'Юридический',      icon: Scale,      desc: 'Демо-сценарий вопросов об условиях', prompt: 'Демо: пример вопросов для самостоятельного изучения условий. Мониторинг и правовая проверка не выполняются.' },
  { id: 'products',    name: 'Товары',           icon: Package,    desc: 'Демо-сценарий обсуждения товара',   prompt: 'Демо: пример обсуждения товара. Каталог магазина и данные о спросе не подключены.' },
  { id: 'finance',     name: 'Финансы',          icon: BarChart2,  desc: 'Демо-сценарий вопросов о расходах', prompt: 'Демо: пример вопросов о финансовых показателях. Отчёты магазина не загружаются, расчёты не выполняются.' },
  { id: 'advertising', name: 'Реклама',          icon: Zap,        desc: 'Демо-сценарий обсуждения рекламы', prompt: 'Демо: пример обсуждения рекламных данных. Кампании и ставки не подключены и не изменяются.' },
]

const DEMO_RESPONSES: Record<string, string> = {
  pricing: 'Демо-сценарий цены: сравните исходные данные и варианты решения. Это пример диалога, а не расчёт цены или рекомендация для вашего магазина.',
  reviews: 'Демо-сценарий отзыва: сначала уточните вопрос покупателя, затем подготовьте черновик ответа. Текст не отправляется покупателю.',
  legal: 'Демо-сценарий условий: составьте список вопросов для самостоятельной проверки в первоисточниках. Это пример диалога, не правовой анализ.',
  products: 'Демо-сценарий товара: перечислите характеристики и вопросы для изучения. Это пример диалога, не анализ спроса или прибыльности.',
  finance: 'Демо-сценарий расходов: перечислите исходные показатели для будущего расчёта. Это пример диалога, а не анализ финансов магазина.',
  advertising: 'Демо-сценарий рекламы: составьте список показателей для сравнения. Это пример диалога, не анализ кампаний или изменение ставок.',
}

function now() {
  return new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

export default function AIAgentsPage() {
  const router    = useRouter()
  const [activeId, setActiveId]   = useState('pricing')
  const [messages, setMessages]   = useState<Record<string, Message[]>>({})
  const [input,    setInput]      = useState('')
  const [typing,   setTyping]     = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const replyTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const agent = AGENTS.find(a => a.id === activeId)!

  useEffect(() => {
    if (!messages[activeId]) {
      setMessages(prev => ({
        ...prev,
        [activeId]: [{ role: 'agent', text: agent.prompt, ts: now() }],
      }))
    }
  }, [activeId, agent.prompt, messages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typing])

  useEffect(() => () => {
    if (replyTimer.current !== null) clearTimeout(replyTimer.current)
    replyTimer.current = null
  }, [])

  function selectAgent(id: string) {
    if (id === activeId) return
    if (replyTimer.current !== null) clearTimeout(replyTimer.current)
    replyTimer.current = null
    setTyping(false)
    setInput('')
    setActiveId(id)
  }

  function send() {
    const text = input.trim()
    if (!text || replyTimer.current !== null) return
    setInput('')

    const userMsg: Message = { role: 'user', text, ts: now() }
    setMessages(prev => ({ ...prev, [activeId]: [...(prev[activeId] ?? []), userMsg] }))

    setTyping(true)
    const reply = DEMO_RESPONSES[activeId]

    const timer = setTimeout(() => {
      if (replyTimer.current !== timer) return
      replyTimer.current = null
      setMessages(prev => ({
        ...prev,
        [activeId]: [...(prev[activeId] ?? []), { role: 'agent', text: reply, ts: now() }],
      }))
      setTyping(false)
    }, 1200)
    replyTimer.current = timer
  }

  const msgs = messages[activeId] ?? []

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-56px)]">

        {/* Agent list */}
        <div className="shrink-0 flex flex-col" style={{ width: 240, background: 'var(--bg)', borderRight: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="p-5 pb-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <button
              onClick={() => router.back()}
              className="flex items-center gap-1 text-[12px] mb-3"
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: 0 }}
              onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-3)' }}
            >
              <ArrowLeft size={12} /> Назад
            </button>
            <p className="label">ИИ-АГЕНТЫ · ДЕМО</p>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-1">
            {AGENTS.map(a => {
              const Icon    = a.icon
              const isActive = a.id === activeId
              const msgCount = messages[a.id]?.filter(m => m.role === 'user').length ?? 0
              return (
                <button
                  key={a.id}
                  onClick={() => selectAgent(a.id)}
                  className="w-full text-left p-4 rounded-[8px] transition-all duration-200"
                  style={{
                    background: isActive ? 'rgba(110,106,252,0.10)' : 'transparent',
                    border: `1px solid ${isActive ? 'var(--violet-text)' : 'transparent'}`,
                    cursor: 'pointer',
                  }}
                >
                  <div className="flex items-center gap-2.5 mb-1.5">
                    <Icon size={13} style={{ color: isActive ? 'var(--violet-text)' : 'var(--text-2)', flexShrink: 0 }} />
                    <span className="text-[13px] font-medium" style={{ color: isActive ? '#FFFFFF' : 'var(--text-3)' }}>{a.name}</span>
                    {msgCount > 0 && (
                      <span className="ml-auto text-[10px]" style={{ color: 'var(--text-2)' }}>{msgCount}</span>
                    )}
                  </div>
                  <p className="text-[11px] line-clamp-2" style={{ color: 'var(--text-2)' }}>{a.desc}</p>
                </button>
              )
            })}
          </div>
        </div>

        {/* Chat */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Chat header */}
          <div className="flex items-center gap-3 px-6 py-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="w-8 h-8 rounded-[8px] flex items-center justify-center" style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <agent.icon size={14} style={{ color: 'var(--violet-text)' }} />
            </div>
            <div>
              <p className="text-[14px] font-semibold" style={{ color: '#FFFFFF' }}>{agent.name}</p>
              <p className="text-[12px]" style={{ color: 'var(--text-2)' }}>{agent.desc}</p>
              <p role="note" aria-label="Демонстрационный режим" className="text-[12px]" style={{ color: 'var(--text-2)' }}>
                ДЕМО. Данные магазина, ИИ и внешние сервисы не подключены. Введённый текст остаётся только в памяти этой страницы, не сохраняется и не отправляется.
              </p>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {msgs.map((m, i) => (
              <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                {m.role === 'agent' && (
                  <div className="w-7 h-7 rounded-[8px] flex items-center justify-center shrink-0 mt-0.5" style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}>
                    <Bot size={12} style={{ color: 'var(--violet-text)' }} />
                  </div>
                )}
                <div style={{ maxWidth: '70%' }}>
                  <div
                    className="px-4 py-3 rounded-[8px] text-[13px] whitespace-pre-line"
                    style={{
                      background: m.role === 'user' ? 'rgba(110,106,252,0.10)' : 'var(--surface)',
                      border: `1px solid ${m.role === 'user' ? 'rgba(110,106,252,0.22)' : 'rgba(255,255,255,0.08)'}`,
                      color: '#FFFFFF',
                      lineHeight: 1.6,
                    }}
                  >
                    {m.text}
                  </div>
                  <p className="text-[10px] mt-1 px-1" style={{ color: 'var(--text-2)', textAlign: m.role === 'user' ? 'right' : 'left' }}>{m.ts}</p>
                </div>
              </div>
            ))}

            {typing && (
              <div className="flex gap-3">
                <div className="w-7 h-7 rounded-[8px] flex items-center justify-center shrink-0" style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <Bot size={12} style={{ color: 'var(--violet-text)' }} />
                </div>
                <div className="px-4 py-3 rounded-[8px]" style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <div className="flex gap-1.5">
                    {[0,1,2].map(i => (
                      <span key={i} className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--text-2)', animationDelay: `${i*200}ms` }} />
                    ))}
                  </div>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="p-5" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="flex gap-3">
              <Input
                placeholder={`Спросить у агента «${agent.name}»...`}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                style={{ flex: 1 }}
              />
              <Button onClick={send} disabled={!input.trim() || typing} style={{ height: 44, paddingLeft: 20, paddingRight: 20 }}>
                <Send size={14} />
              </Button>
            </div>
          </div>
        </div>

      </div>
    </AppShell>
  )
}

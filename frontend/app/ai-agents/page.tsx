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
  { id: 'pricing',     name: 'Ценообразование', icon: TrendingUp, desc: 'Анализ конкурентов и оптимальная цена',       prompt: 'Привет! Я помогу вам определить оптимальную цену. Какой товар анализируем?' },
  { id: 'reviews',     name: 'Отзывы',          icon: Star,       desc: 'Умные ответы на негативные отзывы',           prompt: 'Привет! Покажите мне отзыв, на который нужно ответить — подготовлю профессиональный ответ.' },
  { id: 'legal',       name: 'Юридический',      icon: Scale,      desc: 'Мониторинг оферт и правовых рисков',         prompt: 'Привет! Отслеживаю изменения в офертах маркетплейсов. Какой раздел вас интересует?' },
  { id: 'products',    name: 'Товары',           icon: Package,    desc: 'Поиск прибыльных ниш и анализ спроса',       prompt: 'Привет! Помогу найти перспективные товары для продажи. В какой категории работаете?' },
  { id: 'finance',     name: 'Финансы',          icon: BarChart2,  desc: 'Анализ прибыли, маржи и расходов',          prompt: 'Привет! Разберём финансовые показатели вашего магазина. С чего начнём?' },
  { id: 'advertising', name: 'Реклама',          icon: Zap,        desc: 'Оптимизация ставок и рекламных кампаний',   prompt: 'Привет! Проанализирую ваши рекламные кампании. Какой маркетплейс?' },
]

const DEMO_RESPONSES: Record<string, string[]> = {
  pricing: [
    'Анализирую конкурентов в вашей категории...',
    'По данным на сегодня, медианная цена в нише — 1 240 ₽. Ваша цена 1 490 ₽ на 20% выше медианы — рекомендую снизить до 1 190 ₽ для увеличения конверсии.',
    'Оптимальный диапазон: 1 150–1 250 ₽. При такой цене маржа составит около 22% с учётом всех расходов.',
  ],
  reviews: [
    'Анализирую тональность отзыва...',
    'Рекомендуемый ответ:\n\n«Здравствуйте! Нам очень жаль, что товар не оправдал ваших ожиданий. Мы обязательно разберёмся в ситуации. Пожалуйста, напишите нам в личные сообщения — компенсируем неудобства.»',
  ],
  default: [
    'Обрабатываю запрос...',
    'Готово! Вот что я нашёл по вашему запросу. Если нужны подробности по какому-то конкретному аспекту — уточните.',
  ],
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

  function send() {
    const text = input.trim()
    if (!text || typing) return
    setInput('')

    const userMsg: Message = { role: 'user', text, ts: now() }
    setMessages(prev => ({ ...prev, [activeId]: [...(prev[activeId] ?? []), userMsg] }))

    setTyping(true)
    const responses = DEMO_RESPONSES[activeId] ?? DEMO_RESPONSES.default
    const reply = responses[Math.floor(Math.random() * responses.length)]

    setTimeout(() => {
      setMessages(prev => ({
        ...prev,
        [activeId]: [...(prev[activeId] ?? []), { role: 'agent', text: reply, ts: now() }],
      }))
      setTyping(false)
    }, 1200)
  }

  const msgs = messages[activeId] ?? []

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-56px)]">

        {/* Agent list */}
        <div className="shrink-0 flex flex-col" style={{ width: 240, background: '#09090B', borderRight: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="p-5 pb-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <button
              onClick={() => router.back()}
              className="flex items-center gap-1 text-[12px] mb-3"
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#71717A', padding: 0 }}
              onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
              onMouseLeave={e => { e.currentTarget.style.color = '#71717A' }}
            >
              <ArrowLeft size={12} /> Назад
            </button>
            <p className="label">ИИ-АГЕНТЫ</p>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-1">
            {AGENTS.map(a => {
              const Icon    = a.icon
              const isActive = a.id === activeId
              const msgCount = messages[a.id]?.filter(m => m.role === 'user').length ?? 0
              return (
                <button
                  key={a.id}
                  onClick={() => setActiveId(a.id)}
                  className="w-full text-left p-4 rounded-[8px] transition-all duration-200"
                  style={{
                    background: isActive ? 'rgba(110,106,252,0.10)' : 'transparent',
                    border: `1px solid ${isActive ? '#A78BFA' : 'transparent'}`,
                    cursor: 'pointer',
                  }}
                >
                  <div className="flex items-center gap-2.5 mb-1.5">
                    <Icon size={13} style={{ color: isActive ? '#A78BFA' : '#909096', flexShrink: 0 }} />
                    <span className="text-[13px] font-medium" style={{ color: isActive ? '#FFFFFF' : '#71717A' }}>{a.name}</span>
                    {msgCount > 0 && (
                      <span className="ml-auto text-[10px]" style={{ color: '#909096' }}>{msgCount}</span>
                    )}
                  </div>
                  <p className="text-[11px] line-clamp-2" style={{ color: '#909096' }}>{a.desc}</p>
                </button>
              )
            })}
          </div>
        </div>

        {/* Chat */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Chat header */}
          <div className="flex items-center gap-3 px-6 py-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="w-8 h-8 rounded-[8px] flex items-center justify-center" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
              <agent.icon size={14} style={{ color: '#A78BFA' }} />
            </div>
            <div>
              <p className="text-[14px] font-semibold" style={{ color: '#FFFFFF' }}>{agent.name}</p>
              <p className="text-[12px]" style={{ color: '#909096' }}>{agent.desc}</p>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {msgs.map((m, i) => (
              <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                {m.role === 'agent' && (
                  <div className="w-7 h-7 rounded-[8px] flex items-center justify-center shrink-0 mt-0.5" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
                    <Bot size={12} style={{ color: '#A78BFA' }} />
                  </div>
                )}
                <div style={{ maxWidth: '70%' }}>
                  <div
                    className="px-4 py-3 rounded-[8px] text-[13px] whitespace-pre-line"
                    style={{
                      background: m.role === 'user' ? 'rgba(110,106,252,0.10)' : '#111113',
                      border: `1px solid ${m.role === 'user' ? 'rgba(110,106,252,0.22)' : 'rgba(255,255,255,0.08)'}`,
                      color: '#FFFFFF',
                      lineHeight: 1.6,
                    }}
                  >
                    {m.text}
                  </div>
                  <p className="text-[10px] mt-1 px-1" style={{ color: '#909096', textAlign: m.role === 'user' ? 'right' : 'left' }}>{m.ts}</p>
                </div>
              </div>
            ))}

            {typing && (
              <div className="flex gap-3">
                <div className="w-7 h-7 rounded-[8px] flex items-center justify-center shrink-0" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <Bot size={12} style={{ color: '#A78BFA' }} />
                </div>
                <div className="px-4 py-3 rounded-[8px]" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <div className="flex gap-1.5">
                    {[0,1,2].map(i => (
                      <span key={i} className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#909096', animationDelay: `${i*200}ms` }} />
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

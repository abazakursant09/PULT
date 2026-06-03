'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Search, Plus, MessageSquare, Heart, Eye, Pin, ArrowLeft } from 'lucide-react'

interface Thread {
  id:    string
  name:  string
  count: number
  pin?:  boolean
}

interface Post {
  id:        string
  threadId:  string
  author:    string
  avatar:    string
  title:     string
  body:      string
  likes:     number
  views:     number
  replies:   number
  ts:        string
  liked:     boolean
}

const THREADS: Thread[] = [
  { id: 'general',    name: 'Общее',              count: 128, pin: true },
  { id: 'pricing',    name: 'Ценообразование',    count: 84 },
  { id: 'ads',        name: 'Реклама и продвиж.', count: 67 },
  { id: 'products',   name: 'Поиск товаров',      count: 45 },
  { id: 'legal',      name: 'Документы и право',  count: 23 },
]

const MOCK_POSTS: Post[] = [
  {
    id: '1', threadId: 'general', author: 'Алексей М.', avatar: 'А',
    title: 'Как я поднял конверсию на 40% за месяц',
    body: 'Поделюсь кейсом: изменил главное фото и заголовок, добавил ключи из нижней выдачи. Результат — CTR с 2.1% до 3.8%, конверсия с 4.3% до 6.0%.',
    likes: 48, views: 312, replies: 14, ts: '2 ч назад', liked: false,
  },
  {
    id: '2', threadId: 'general', author: 'Марина К.', avatar: 'М',
    title: 'WB снизил комиссию на электронику — успели поднять цену?',
    body: 'Вчера пришло уведомление. Для категории "Электроника" комиссия снижается с 15% до 12% с 1 июня. Успел пересчитать маржу — прирост ~3.2% к прибыли.',
    likes: 31, views: 189, replies: 7, ts: '4 ч назад', liked: true,
  },
  {
    id: '3', threadId: 'general', author: 'Дмитрий О.', avatar: 'Д',
    title: 'Топ-5 ошибок при запуске первого товара',
    body: '1. Не считать реальную маржу с возвратами. 2. Игнорировать выкупы для ранжирования. 3. Запускать рекламу до набора отзывов. 4. Широкая семантика без минус-слов. 5. Один SKU вместо размерной линейки.',
    likes: 92, views: 541, replies: 28, ts: '1 д назад', liked: false,
  },
  {
    id: '4', threadId: 'pricing', author: 'Светлана В.', avatar: 'С',
    title: 'Автоматическое снижение цены — когда это помогает, когда вредит',
    body: 'Протестировала автоакции на 12 SKU. Вывод: работает только если минимальная маржа ≥18%. Ниже — идёте в минус быстрее, чем растёт позиция.',
    likes: 37, views: 204, replies: 11, ts: '6 ч назад', liked: false,
  },
  {
    id: '5', threadId: 'ads', author: 'Роман Б.', avatar: 'Р',
    title: 'ACoS < 15% — реально ли на Ozon в 2025?',
    body: 'Достиг ACoS 13.2% на категории "Уход за кожей". Секрет: жёсткий контроль ставок каждые 6 часов + отключение неэффективных ключей через 3 дня без заказов.',
    likes: 55, views: 290, replies: 19, ts: '8 ч назад', liked: false,
  },
]

export default function CommunityPage() {
  const router = useRouter()
  const [activeThread, setActiveThread] = useState('general')
  const [search, setSearch]             = useState('')
  const [posts, setPosts]               = useState<Post[]>(MOCK_POSTS)
  const [showNew, setShowNew]           = useState(false)
  const [newTitle, setNewTitle]         = useState('')
  const [newBody, setNewBody]           = useState('')

  const filtered = posts.filter(p =>
    p.threadId === activeThread &&
    (search === '' || p.title.toLowerCase().includes(search.toLowerCase()) || p.body.toLowerCase().includes(search.toLowerCase()))
  )

  function toggleLike(id: string) {
    setPosts(ps => ps.map(p => p.id === id ? { ...p, liked: !p.liked, likes: p.liked ? p.likes - 1 : p.likes + 1 } : p))
  }

  function handlePost() {
    if (!newTitle.trim()) return
    const post: Post = {
      id:       Date.now().toString(),
      threadId: activeThread,
      author:   'Вы',
      avatar:   'В',
      title:    newTitle.trim(),
      body:     newBody.trim(),
      likes:    0, views: 0, replies: 0,
      ts:       'только что',
      liked:    false,
    }
    setPosts(ps => [post, ...ps])
    setNewTitle('')
    setNewBody('')
    setShowNew(false)
  }

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-56px)]">

        {/* Threads sidebar */}
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
            <p className="label">РАЗДЕЛЫ</p>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-0.5">
            {THREADS.map(t => {
              const isActive = t.id === activeThread
              return (
                <button
                  key={t.id}
                  onClick={() => setActiveThread(t.id)}
                  className="w-full text-left px-3 py-2.5 rounded-[8px] flex items-center justify-between transition-all duration-200"
                  style={{
                    background: isActive ? 'rgba(110,106,252,0.08)' : 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                >
                  <div className="flex items-center gap-2">
                    {t.pin && <Pin size={10} style={{ color: '#909096', flexShrink: 0 }} />}
                    <span className="text-[13px]" style={{ color: isActive ? '#A78BFA' : '#71717A' }}>{t.name}</span>
                  </div>
                  <span className="text-[11px] mono" style={{ color: '#909096' }}>{t.count}</span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Posts */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Toolbar */}
          <div className="flex items-center gap-3 px-6 py-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="relative flex-1">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: '#909096' }} />
              <Input
                placeholder="Поиск по постам..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                style={{ paddingLeft: 36 }}
              />
            </div>
            <Button onClick={() => setShowNew(v => !v)} style={{ height: 44, paddingLeft: 20, paddingRight: 20 }}>
              <Plus size={14} /> Новый пост
            </Button>
          </div>

          {/* New post form */}
          {showNew && (
            <div className="mx-6 mt-5 p-6 rounded-[8px]" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
              <p className="text-[13px] font-semibold mb-4" style={{ color: '#FFFFFF' }}>Новый пост</p>
              <div className="space-y-3">
                <div>
                  <label className="label mb-2">ЗАГОЛОВОК</label>
                  <Input placeholder="О чём ваш пост?" value={newTitle} onChange={e => setNewTitle(e.target.value)} />
                </div>
                <div>
                  <label className="label mb-2">ТЕКСТ</label>
                  <textarea
                    placeholder="Поделитесь опытом..."
                    value={newBody}
                    onChange={e => setNewBody(e.target.value)}
                    rows={4}
                    style={{
                      width: '100%', background: '#18181B', border: '1px solid rgba(255,255,255,0.08)',
                      borderRadius: 8, padding: '12px 14px', color: '#FFFFFF',
                      fontSize: 13, resize: 'vertical', outline: 'none',
                      fontFamily: 'inherit', lineHeight: 1.6,
                    }}
                    onFocus={e => { e.currentTarget.style.borderColor = '#7C3AED' }}
                    onBlur={e => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)' }}
                  />
                </div>
                <div className="flex gap-3">
                  <Button onClick={handlePost}>Опубликовать</Button>
                  <Button variant="ghost" onClick={() => { setShowNew(false); setNewTitle(''); setNewBody('') }}>Отмена</Button>
                </div>
              </div>
            </div>
          )}

          {/* Post list */}
          <div className="flex-1 overflow-y-auto p-6 space-y-3">
            {filtered.length === 0 ? (
              <div className="py-20 text-center">
                <MessageSquare size={32} className="mx-auto mb-4" style={{ color: '#3A3A40' }} />
                <p className="text-[15px] font-medium mb-2" style={{ color: '#71717A' }}>Постов пока нет</p>
                <p className="text-[13px]" style={{ color: '#909096' }}>Будьте первым — поделитесь опытом</p>
              </div>
            ) : filtered.map(p => (
              <div
                key={p.id}
                className="p-5 rounded-[8px] transition-colors duration-200"
                style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}
                onMouseEnter={e => { e.currentTarget.style.background = '#18181B' }}
                onMouseLeave={e => { e.currentTarget.style.background = '#111113' }}
              >
                {/* Author row */}
                <div className="flex items-center gap-2.5 mb-3">
                  <div className="w-7 h-7 rounded-[8px] flex items-center justify-center text-[11px] font-bold shrink-0"
                    style={{ background: '#18181B', color: '#A78BFA' }}>
                    {p.avatar}
                  </div>
                  <span className="text-[13px] font-medium" style={{ color: '#FFFFFF' }}>{p.author}</span>
                  <span className="text-[12px]" style={{ color: '#909096' }}>{p.ts}</span>
                </div>

                {/* Content */}
                <p className="text-[14px] font-semibold mb-2" style={{ color: '#FFFFFF' }}>{p.title}</p>
                {p.body && (
                  <p className="text-[13px] mb-4 line-clamp-3" style={{ color: '#71717A', lineHeight: 1.6 }}>{p.body}</p>
                )}

                {/* Meta row */}
                <div className="flex items-center gap-5">
                  <button
                    onClick={() => toggleLike(p.id)}
                    className="flex items-center gap-1.5 text-[12px] transition-colors duration-200"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: p.liked ? '#A78BFA' : '#909096' }}
                  >
                    <Heart size={12} fill={p.liked ? '#A78BFA' : 'none'} />
                    {p.likes}
                  </button>
                  <span className="flex items-center gap-1.5 text-[12px]" style={{ color: '#909096' }}>
                    <MessageSquare size={12} /> {p.replies}
                  </span>
                  <span className="flex items-center gap-1.5 text-[12px]" style={{ color: '#909096' }}>
                    <Eye size={12} /> {p.views}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </AppShell>
  )
}

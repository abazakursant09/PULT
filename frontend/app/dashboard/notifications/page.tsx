'use client'

import { useEffect, useRef, useState } from 'react'
import { Bell, Check, CheckCheck, ChevronLeft, ChevronRight } from 'lucide-react'
import { api, type NotificationItem } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { BlurFade } from '@/components/ui/blur-fade'
import { Skeleton } from '@/components/ui/skeleton'

const DOT_COLOR: Record<string, string> = {
  new_review:    'var(--violet-text)',
  offer_change:  'var(--warning)',
  trial_end:     'var(--danger)',
  limit_reached: 'var(--violet-text)',
  referral_paid: 'var(--success)',
}

const TYPE_LABEL: Record<string, string> = {
  new_review:    'Отзыв',
  offer_change:  'Оферта',
  trial_end:     'Подписка',
  limit_reached: 'Лимит',
  referral_paid: 'Реферал',
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })
}

const PER_PAGE = 10

export default function NotificationsPage() {
  const [items,   setItems]   = useState<NotificationItem[]>([])
  const [total,   setTotal]   = useState(0)
  const [unread,  setUnread]  = useState(0)
  const [page,    setPage]    = useState(1)
  const [loading, setLoading] = useState(true)
  const [tab,     setTab]     = useState('all')
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const requestId = useRef(0)
  const loadingRef = useRef(true)
  const savingRef = useRef(false)

  useEffect(() => {
    void load(page)
    return () => { requestId.current += 1 }
  }, [page])

  async function load(p: number) {
    if (savingRef.current) return
    const current = ++requestId.current
    loadingRef.current = true
    setLoading(true)
    setLoadError('')
    setActionError('')
    setMessage('')
    try {
      const d = await api.notifications.list(p, PER_PAGE)
      if (current !== requestId.current) return
      setItems(d.items)
      setTotal(d.total)
      setUnread(d.unread_count)
    } catch {
      if (current === requestId.current) setLoadError('Не удалось загрузить уведомления. Повторите загрузку.')
    } finally {
      if (current === requestId.current) {
        loadingRef.current = false
        setLoading(false)
      }
    }
  }

  async function markRead(id?: string) {
    if (savingRef.current || loadingRef.current || loadError) return
    if (id ? !items.some(item => item.id === id && !item.is_read) : unread === 0) return
    const current = requestId.current
    savingRef.current = true
    setSaving(true)
    setActionError('')
    setMessage('')
    try {
      const result = id ? await api.notifications.markRead(id) : await api.notifications.markAllRead()
      if (!result.ok) throw new Error('Read not confirmed')
      if (current !== requestId.current) return
      setItems(ns => ns.map(n => !id || n.id === id ? { ...n, is_read: true } : n))
      setUnread(c => id ? Math.max(0, c - 1) : 0)
      setMessage(id ? 'Уведомление отмечено прочитанным.' : 'Все уведомления отмечены прочитанными.')
    } catch {
      if (current === requestId.current) setActionError(id
        ? 'Не удалось отметить уведомление прочитанным. Повторите попытку.'
        : 'Не удалось отметить все уведомления прочитанными. Повторите попытку.')
    } finally {
      savingRef.current = false
      if (current === requestId.current) setSaving(false)
    }
  }

  // There is no seed() any more. It called POST /api/notifications/seed, which invents
  // notifications about events that never happened — a review left on "Умная колонка XL",
  // a trial ending in three days. It was opt-in and labelled "демо", but once seeded those
  // rows are indistinguishable from real ones everywhere else in the product, including the
  // bell. A notification is a statement that something happened to THIS seller, so there is
  // no honest way to fabricate one. The backend endpoint is left in place, simply unreachable.

  const totalPages = Math.ceil(total / PER_PAGE)
  const filteredItems = tab === 'unread' ? items.filter(n => !n.is_read) : items

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)' }}>
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-10">

        {/* Header */}
        <BlurFade inView>
          <div className="flex items-center justify-between mb-8 flex-wrap gap-3">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-2xl flex items-center justify-center shadow-stripe" style={{ background: 'var(--violet-dim)' }}>
                <Bell size={20} style={{ color: 'var(--violet-text)' }} />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-xl font-bold" style={{ color: 'var(--text)', letterSpacing: '-0.02em' }}>Уведомления</h1>
                  {!loading && !loadError && unread > 0 && <Badge variant="default" className="text-xs">{unread}</Badge>}
                </div>
                {!loading && !loadError && unread > 0 && <p className="text-xs text-muted-foreground">{unread} непрочитанных</p>}
              </div>
            </div>

            <div className="flex items-center gap-2">
              {!loading && !loadError && unread > 0 && (
                <Button variant="ghost" size="sm" onClick={() => markRead()} disabled={saving} className="flex items-center gap-1.5 text-xs">
                  <CheckCheck size={13} /> Прочитать все
                </Button>
              )}
            </div>
          </div>
        </BlurFade>

        {actionError && <p role="alert" className="mb-4 text-sm" style={{ color: 'var(--danger)' }}>{actionError}</p>}
        {message && <p role="status" className="mb-4 text-sm" style={{ color: 'var(--text-2)' }}>{message}</p>}
        <BlurFade inView delay={0.05}>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="mb-5">
              <TabsTrigger value="all">Все <span className="ml-1.5 text-xs opacity-60">{loading || loadError ? '—' : total}</span></TabsTrigger>
              <TabsTrigger value="unread">Непрочитанные <span className="ml-1.5 text-xs opacity-60">{loading || loadError ? '—' : unread}</span></TabsTrigger>
            </TabsList>

            <TabsContent value={tab}>
              {loading ? (
                <div className="space-y-3">
                  {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
                </div>
              ) : loadError ? (
                <Card className="shadow-stripe">
                  <CardContent className="py-10 text-center">
                    <p role="alert" className="mb-4 text-sm" style={{ color: 'var(--danger)' }}>{loadError}</p>
                    <Button variant="secondary" onClick={() => load(page)} disabled={saving}>Повторить загрузку</Button>
                  </CardContent>
                </Card>
              ) : filteredItems.length === 0 ? (
                <Card className="shadow-stripe">
                  <CardContent className="flex flex-col items-center py-16 text-center">
                    <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-4" style={{ background: 'color-mix(in srgb, var(--violet) 8%, transparent)' }}>
                      <Bell size={24} style={{ color: 'var(--violet-text)' }} />
                    </div>
                    <p className="font-semibold mb-2" style={{ color: 'var(--text)' }}>Уведомлений пока нет</p>
                    <p className="text-sm text-muted-foreground">Здесь появятся события по вашему бизнесу</p>
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-2">
                  {filteredItems.map((n, idx) => (
                    <BlurFade key={n.id} delay={idx * 0.04} inView>
                      <Card className={`shadow-stripe border-border/60 transition-all ${!n.is_read ? 'border-l-4' : ''}`}
                            style={!n.is_read ? { borderLeftColor: DOT_COLOR[n.type] ?? 'var(--violet-text)' } : {}}>
                        <CardContent className="p-4 flex gap-3 items-start">
                          <div className="w-2 h-2 rounded-full mt-2 shrink-0" style={{ background: n.is_read ? 'hsl(var(--border))' : (DOT_COLOR[n.type] ?? 'var(--violet-text)') }} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between gap-3 flex-wrap">
                              <div className="min-w-0">
                                <p className="font-medium text-sm" style={{ color: n.is_read ? 'var(--text-2)' : 'var(--text)' }}>{n.title}</p>
                                <p className="text-sm text-muted-foreground mt-0.5 leading-relaxed">{n.message}</p>
                              </div>
                              {!n.is_read && (
                                <Button variant="ghost" size="sm" onClick={() => markRead(n.id)} disabled={saving} className="shrink-0 flex items-center gap-1 text-xs h-7">
                                  <Check size={10} /> Прочитать
                                </Button>
                              )}
                            </div>
                            <div className="flex items-center gap-2 mt-2.5">
                              <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" style={{ background: `color-mix(in srgb, ${DOT_COLOR[n.type] ?? 'var(--violet-text)'} 8%, transparent)`, color: DOT_COLOR[n.type] ?? 'var(--violet-text)' }}>
                                {TYPE_LABEL[n.type] ?? n.type}
                              </span>
                              <span className="text-xs text-muted-foreground">{fmt(n.created_at)}</span>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    </BlurFade>
                  ))}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </BlurFade>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-3 mt-8">
            <Button variant="ghost" size="sm" aria-label="Предыдущая страница" onClick={() => { if (!savingRef.current && !loadingRef.current) setPage(p => Math.max(1, p - 1)) }} disabled={page === 1 || loading || saving}>
              <ChevronLeft size={16} />
            </Button>
            <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
            <Button variant="ghost" size="sm" aria-label="Следующая страница" onClick={() => { if (!savingRef.current && !loadingRef.current) setPage(p => Math.min(totalPages, p + 1)) }} disabled={page === totalPages || loading || saving}>
              <ChevronRight size={16} />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

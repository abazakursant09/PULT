'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Bell, RefreshCw, AlertTriangle, Zap, Info, ArrowLeft, ShieldAlert } from 'lucide-react'
import { api, type MonitorEvent } from '@/lib/api'
import { MonitorEventCard } from '@/components/MonitorEventCard'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { BlurFade } from '@/components/ui/blur-fade'
import { Skeleton } from '@/components/ui/skeleton'

function isAuthError(msg: string) {
  return msg.includes('401') || msg.includes('403') || msg.includes('Not authenticated') || msg.includes('Недействительный токен') || msg.includes('Пользователь не найден')
}

const SEV_META = {
  critical:  { label: 'Критические',  Icon: AlertTriangle, color: '#dc2626', bg: 'rgba(220,38,38,0.08)'  },
  important: { label: 'Важные',       Icon: Zap,           color: '#d97706', bg: 'rgba(217,119,6,0.08)'  },
  info:      { label: 'Информация',   Icon: Info,          color: '#1A73E8', bg: 'rgba(26,115,232,0.08)' },
} as const

export default function MonitorPage() {
  const router = useRouter()

  const [events,   setEvents]   = useState<MonitorEvent[]>([])
  const [loading,  setLoading]  = useState(true)
  const [checking, setChecking] = useState(false)
  const [error,    setError]    = useState('')
  const [tab,      setTab]      = useState('all')

  const load = useCallback(async () => {
    try {
      setEvents(await api.monitor.list())
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : ''
      if (isAuthError(msg)) router.push('/login')
      else setError(msg || 'Ошибка загрузки событий')
    }
  }, [router])

  useEffect(() => {
    if (!localStorage.getItem('token')) { router.push('/login'); return }
    setLoading(true)
    load().finally(() => setLoading(false))
  }, [load, router])

  async function handleCheck() {
    setChecking(true); setError('')
    try {
      const newEvents = await api.monitor.check()
      setEvents(prev => {
        const ids = new Set(newEvents.map(e => e.id))
        return [...newEvents, ...prev.filter(e => !ids.has(e.id))]
      })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка проверки'
      if (isAuthError(msg)) router.push('/login')
      else setError(msg)
    } finally { setChecking(false) }
  }

  const filteredEvents = tab === 'all' ? events : events.filter(e => e.severity === tab)
  const countBySev = (sev: string) => events.filter(e => e.severity === sev).length
  const grouped = (['critical', 'important', 'info'] as const)
    .map(sev => ({ sev, items: filteredEvents.filter(e => e.severity === sev).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()) }))
    .filter(g => g.items.length > 0)

  return (
    <div className="min-h-screen" style={{ background: '#F6F9FC' }}>
      <div className="max-w-6xl mx-auto px-5 sm:px-8 py-8">

        {/* Back */}
        <button onClick={() => router.push('/dashboard')} className="sm:hidden flex items-center gap-1.5 text-sm mb-5 text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft size={14} /> Назад
        </button>

        {/* Header */}
        <BlurFade inView>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl flex items-center justify-center shadow-stripe" style={{ background: '#1A73E8' }}>
                <Bell size={22} style={{ color: 'white' }} />
              </div>
              <div>
                <h1 className="text-2xl font-bold" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>Пульт-Монитор</h1>
                <p className="text-sm text-muted-foreground">Система раннего оповещения об изменениях на маркетплейсах</p>
              </div>
            </div>
            <Button onClick={handleCheck} disabled={checking} className="flex items-center gap-2 shrink-0">
              <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
              {checking ? 'Проверяем...' : 'Проверить обновления'}
            </Button>
          </div>
        </BlurFade>

        {/* Stat cards */}
        <BlurFade inView delay={0.05}>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
            {[
              { label: 'Всего', count: events.length,       color: '#0A2540', bg: 'white' },
              { label: 'Критических', count: countBySev('critical'), color: '#dc2626', bg: 'rgba(220,38,38,0.06)' },
              { label: 'Важных',      count: countBySev('important'), color: '#d97706', bg: 'rgba(217,119,6,0.06)' },
              { label: 'Инфо',        count: countBySev('info'),      color: '#1A73E8', bg: 'rgba(26,115,232,0.06)' },
            ].map(({ label, count, color, bg }) => (
              <Card key={label} className="shadow-stripe border-border/60">
                <CardContent className="p-4">
                  <p className="text-xs text-muted-foreground mb-1">{label}</p>
                  <p className="text-2xl font-bold" style={{ color }}>{count}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </BlurFade>

        {/* Error */}
        {error && (
          <div className="flex items-start justify-between gap-3 px-4 py-3 rounded-xl mb-6 text-sm" style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#b91c1c' }}>
            <span>{error}</span>
            <button onClick={() => setError('')} className="shrink-0 opacity-60 hover:opacity-100">✕</button>
          </div>
        )}

        {/* Tabs */}
        <BlurFade inView delay={0.1}>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="mb-6">
              <TabsTrigger value="all">Все <span className="ml-1.5 text-xs opacity-60">{events.length}</span></TabsTrigger>
              <TabsTrigger value="critical">Критические <span className="ml-1.5 text-xs opacity-60">{countBySev('critical')}</span></TabsTrigger>
              <TabsTrigger value="important">Важные <span className="ml-1.5 text-xs opacity-60">{countBySev('important')}</span></TabsTrigger>
              <TabsTrigger value="info">Инфо <span className="ml-1.5 text-xs opacity-60">{countBySev('info')}</span></TabsTrigger>
            </TabsList>

            <TabsContent value={tab}>
              {loading ? (
                <div className="space-y-4">
                  {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
                </div>
              ) : filteredEvents.length === 0 && !error ? (
                <Card className="shadow-stripe">
                  <CardContent className="flex flex-col items-center justify-center py-20 text-center">
                    <div className="w-16 h-16 rounded-2xl flex items-center justify-center mb-5" style={{ background: 'rgba(26,115,232,0.08)', border: '1px solid rgba(26,115,232,0.15)' }}>
                      <ShieldAlert size={28} style={{ color: '#1A73E8' }} />
                    </div>
                    <h3 className="font-semibold text-lg mb-2" style={{ color: '#0A2540' }}>Нет активных оповещений</h3>
                    <p className="text-sm text-muted-foreground mb-6 max-w-xs">
                      Нажмите «Проверить обновления», чтобы получить актуальные события с маркетплейсов
                    </p>
                    <Button onClick={handleCheck} disabled={checking}>
                      <RefreshCw size={14} className={checking ? 'animate-spin mr-2' : 'mr-2'} />
                      {checking ? 'Проверяем...' : 'Проверить обновления'}
                    </Button>
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-8">
                  {grouped.map(({ sev, items }) => {
                    const meta = SEV_META[sev]
                    const MetaIcon = meta.Icon
                    return (
                      <section key={sev}>
                        <div className="flex items-center gap-3 mb-4">
                          <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: meta.bg }}>
                            <MetaIcon size={14} style={{ color: meta.color }} />
                          </div>
                          <h2 className="font-semibold text-sm" style={{ color: meta.color }}>{meta.label}</h2>
                          <Badge variant="secondary" className="text-xs">{items.length}</Badge>
                          <div className="flex-1 h-px" style={{ background: 'hsl(var(--border))' }} />
                        </div>
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                          {items.map((ev, idx) => (
                            <BlurFade key={ev.id} delay={idx * 0.05} inView>
                              <MonitorEventCard event={ev} />
                            </BlurFade>
                          ))}
                        </div>
                      </section>
                    )
                  })}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </BlurFade>
      </div>
    </div>
  )
}

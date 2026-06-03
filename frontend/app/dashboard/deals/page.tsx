'use client'

import { useEffect, useState } from 'react'
import { api, DealEntry } from '@/lib/api'
import { FileText, CheckCircle, Clock, Truck, Package, XCircle, Star, ChevronDown, ChevronUp, Briefcase } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { BlurFade } from '@/components/ui/blur-fade'

const STATUS_CONFIG: Record<string, { label: string; variant: 'default' | 'secondary' | 'outline' | 'destructive'; icon: React.ReactNode }> = {
  draft:     { label: 'Черновик',    variant: 'outline',      icon: <FileText className="w-3 h-3" /> },
  agreed:    { label: 'Согласовано', variant: 'default',      icon: <CheckCircle className="w-3 h-3" /> },
  paid:      { label: 'Оплачено',    variant: 'default',      icon: <CheckCircle className="w-3 h-3" /> },
  shipped:   { label: 'Отгружено',   variant: 'secondary',    icon: <Truck className="w-3 h-3" /> },
  delivered: { label: 'Доставлено',  variant: 'default',      icon: <Package className="w-3 h-3" /> },
  cancelled: { label: 'Отменено',    variant: 'destructive',  icon: <XCircle className="w-3 h-3" /> },
}

const STATUS_FLOW: Record<string, string[]> = {
  draft:   ['agreed', 'cancelled'],
  agreed:  ['paid', 'cancelled'],
  paid:    ['shipped', 'cancelled'],
  shipped: ['delivered', 'cancelled'],
}

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] ?? { label: status, variant: 'outline' as const, icon: null }
  return (
    <Badge variant={cfg.variant} className="gap-1 text-xs">
      {cfg.icon} {cfg.label}
    </Badge>
  )
}

function ReviewModal({ deal, onClose }: { deal: DealEntry; onClose: () => void }) {
  const [rating, setRating] = useState(5)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)

  async function submit() {
    setLoading(true); setErr('')
    try {
      await api.supplierReviews.create({
        target_type: 'supplier',
        target_id:   deal.supplier_id,
        deal_id:     deal.id,
        rating,
        text: text || undefined,
      })
      setDone(true)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Ошибка')
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(10,37,64,0.5)', backdropFilter: 'blur(4px)' }}>
      <Card className="shadow-stripe-lg w-full max-w-md">
        <CardHeader className="pb-3 border-b border-border/60">
          <CardTitle className="text-lg" style={{ color: '#0A2540' }}>Оценить поставщика</CardTitle>
          <p className="text-sm text-muted-foreground">Сделка: {deal.product_name}</p>
        </CardHeader>
        {done ? (
          <CardContent className="p-8 text-center">
            <CheckCircle className="w-12 h-12 mx-auto mb-3" style={{ color: '#1A73E8' }} />
            <p className="font-medium mb-4" style={{ color: '#0A2540' }}>Спасибо за отзыв!</p>
            <Button onClick={onClose} className="px-8">Закрыть</Button>
          </CardContent>
        ) : (
          <CardContent className="p-5 space-y-4">
            <div>
              <p className="text-sm font-medium mb-2" style={{ color: '#0A2540' }}>Оценка</p>
              <div className="flex gap-2">
                {[1,2,3,4,5].map(n => (
                  <button key={n} onClick={() => setRating(n)}>
                    <Star className="w-7 h-7" style={{ color: n <= rating ? '#1A73E8' : 'hsl(var(--border))', fill: n <= rating ? '#1A73E8' : 'none' }} />
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="text-sm font-medium mb-2" style={{ color: '#0A2540' }}>Комментарий</p>
              <textarea
                className="w-full border border-border rounded-lg px-3 py-2 text-sm resize-none h-24 bg-background outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-colors"
                placeholder="Поделитесь опытом работы с поставщиком..."
                value={text}
                onChange={e => setText(e.target.value)}
              />
            </div>
            {err && <p className="text-sm text-red-600">{err}</p>}
            <div className="flex gap-3">
              <Button className="flex-1" onClick={submit} disabled={loading}>{loading ? 'Отправка...' : 'Отправить'}</Button>
              <Button variant="outline" onClick={onClose}>Отмена</Button>
            </div>
          </CardContent>
        )}
      </Card>
    </div>
  )
}

function DealCard({ deal, onRefresh }: { deal: DealEntry; onRefresh: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [showReview, setShowReview] = useState(false)

  const nextStatuses = STATUS_FLOW[deal.status] ?? []

  async function advance(status: string) {
    setLoading(true)
    try {
      await api.deals.updateStatus(deal.id, status)
      onRefresh()
    } finally { setLoading(false) }
  }

  async function sign() {
    setLoading(true)
    try {
      await api.deals.sign(deal.id)
      onRefresh()
    } finally { setLoading(false) }
  }

  const fmt = new Intl.NumberFormat('ru-RU')

  return (
    <Card className={`shadow-stripe transition-all ${expanded ? 'border-primary/30' : 'border-border/60'}`}>
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-2 flex-wrap">
              <div>
                <p className="font-semibold" style={{ color: '#0A2540' }}>{deal.product_name}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  #{deal.id.slice(0, 8)} · {new Date(deal.created_at).toLocaleDateString('ru-RU')}
                </p>
              </div>
              <StatusBadge status={deal.status} />
            </div>
            <div className="flex flex-wrap gap-4 mt-2 text-sm text-muted-foreground">
              <span>{deal.quantity} ед. × {fmt.format(deal.price_per_unit)} ₽</span>
              <span className="font-medium" style={{ color: '#0A2540' }}>Итого: {fmt.format(deal.total_price)} ₽</span>
              {deal.deadline && (
                <span className="flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5" />
                  {new Date(deal.deadline).toLocaleDateString('ru-RU')}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => setExpanded(v => !v)}
            className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted transition-colors shrink-0 mt-1"
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>

        {/* Action bar */}
        <div className="flex flex-wrap gap-2 mt-3">
          {deal.status === 'draft' && !deal.signed_by_seller && (
            <Button size="sm" onClick={sign} disabled={loading} className="text-xs">
              Подписать договор
            </Button>
          )}
          {deal.signed_by_seller && deal.status === 'draft' && (
            <span className="text-xs flex items-center gap-1" style={{ color: '#1A73E8' }}>
              <CheckCircle className="w-3.5 h-3.5" /> Подписано {deal.signed_at ? new Date(deal.signed_at).toLocaleDateString('ru-RU') : ''}
            </span>
          )}
          {nextStatuses.filter(s => s !== 'cancelled').map(s => (
            <Button key={s} size="sm" variant="outline" onClick={() => advance(s)} disabled={loading} className="text-xs gap-1" style={{ color: '#1A73E8', borderColor: 'rgba(26,115,232,0.3)' }}>
              → {STATUS_CONFIG[s]?.label}
            </Button>
          ))}
          {nextStatuses.includes('cancelled') && (
            <Button size="sm" variant="outline" onClick={() => advance('cancelled')} disabled={loading} className="text-xs text-red-600 border-red-200 hover:border-red-400">
              Отменить
            </Button>
          )}
          {deal.status === 'delivered' && (
            <Button size="sm" variant="outline" onClick={() => setShowReview(true)} className="text-xs gap-1" style={{ color: '#1A73E8', borderColor: 'rgba(26,115,232,0.3)' }}>
              <Star className="w-3.5 h-3.5" /> Оценить поставщика
            </Button>
          )}
        </div>

        {/* Contract */}
        {expanded && deal.contract_text && (
          <div className="mt-4 pt-4 border-t border-border/60">
            <p className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
              <FileText className="w-3.5 h-3.5" /> Текст договора
            </p>
            <pre className="text-xs bg-muted/50 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed border border-border/40" style={{ color: '#0A2540' }}>
              {deal.contract_text}
            </pre>
          </div>
        )}
      </CardContent>

      {showReview && <ReviewModal deal={deal} onClose={() => { setShowReview(false); onRefresh() }} />}
    </Card>
  )
}

const STATUS_TABS = [
  { value: '',          label: 'Все' },
  { value: 'draft',     label: 'Черновики' },
  { value: 'agreed',    label: 'Согласованные' },
  { value: 'paid',      label: 'Оплаченные' },
  { value: 'shipped',   label: 'В пути' },
  { value: 'delivered', label: 'Доставлены' },
  { value: 'cancelled', label: 'Отменены' },
]

export default function DealsPage() {
  const [deals, setDeals] = useState<DealEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('')

  async function load() {
    setLoading(true)
    try {
      const data = await api.deals.my()
      setDeals(data)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  const filtered = tab ? deals.filter(d => d.status === tab) : deals

  return (
    <>
      <div className="flex-1 p-4 md:p-6" style={{ background: '#F6F9FC' }}>
        <div className="max-w-4xl mx-auto space-y-6">

          <BlurFade inView>
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-2xl flex items-center justify-center shadow-stripe" style={{ background: '#1A73E8' }}>
                <Briefcase size={20} style={{ color: 'white' }} />
              </div>
              <div>
                <h1 className="text-2xl font-bold" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>Мои сделки</h1>
                <p className="text-sm text-muted-foreground mt-0.5">Управление договорами с поставщиками</p>
              </div>
            </div>
          </BlurFade>

          {/* Tabs */}
          <BlurFade inView delay={0.04}>
            <div className="flex gap-1 overflow-x-auto pb-1 flex-wrap">
              {STATUS_TABS.map(t => (
                <button
                  key={t.value}
                  onClick={() => setTab(t.value)}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium whitespace-nowrap transition-all border"
                  style={tab === t.value
                    ? { background: '#1A73E8', color: 'white', borderColor: '#1A73E8' }
                    : { background: 'white', color: '#425466', borderColor: 'hsl(var(--border))' }
                  }
                >
                  {t.label}
                  {t.value && (
                    <span className="ml-1.5 text-xs opacity-70">
                      {deals.filter(d => d.status === t.value).length}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </BlurFade>

          {loading ? (
            <div className="space-y-4">
              {[1,2,3].map(i => <Skeleton key={i} className="h-32 rounded-xl" />)}
            </div>
          ) : filtered.length === 0 ? (
            <Card className="shadow-stripe">
              <CardContent className="p-12 text-center">
                <FileText className="w-10 h-10 mx-auto mb-3 text-muted-foreground/40" />
                <p className="text-sm font-medium mb-1" style={{ color: '#0A2540' }}>Нет сделок</p>
                <p className="text-sm text-muted-foreground">
                  Перейдите в{' '}
                  <a href="/suppliers" className="hover:underline" style={{ color: '#1A73E8' }}>каталог поставщиков</a>
                  , чтобы оформить первую сделку
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {filtered.map((d, idx) => (
                <BlurFade key={d.id} inView delay={idx * 0.05}>
                  <DealCard deal={d} onRefresh={load} />
                </BlurFade>
              ))}
            </div>
          )}

        </div>
      </div>
    </>
  )
}
